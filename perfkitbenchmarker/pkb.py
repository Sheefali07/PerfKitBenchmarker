# Copyright 2014 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runs all benchmarks in PerfKitBenchmarker.

All benchmarks in PerfKitBenchmarker export the following interface:

GetInfo: this returns, the name of the benchmark, the number of machines
          required to run one instance of the benchmark, a detailed description
          of the benchmark, and if the benchmark requires a scratch disk.
Prepare: this function takes a list of VMs as an input parameter. The benchmark
         will then get all binaries required to run the benchmark and, if
         required, create data files.
Run: this function takes a list of VMs as an input parameter. The benchmark will
     then run the benchmark upon the machines specified. The function will
     return a dictonary containing the results of the benchmark.
Cleanup: this function takes a list of VMs as an input parameter. The benchmark
         will then return the machine to the state it was at before Prepare
         was called.

PerfKitBenchmarker has following run stages: prepare, run, cleanup and all.
prepare: PerfKitBenchmarker will read command-line flags, decide which
benchmarks to run
         and create necessary resources for each benchmark, including networks,
         VMs, disks, keys and execute the Prepare function of each benchmark to
         install necessary softwares, upload datafiles, etc and generate a
         run_uri, which can be used to run benchmark multiple times.
run: PerfKitBenchmarker execute the Run function of each benchmark and collect
samples
     generated. Publisher may publish these samples accourding to settings. Run
     stage can be called multiple times with the run_uri generated by prepare
     stage.
cleanup: PerfKitBenchmarker will run Cleanup function of each benchmark to
uninstall
         softwares and delete data files. Then it will delete VMs, key files,
         networks, disks generated in prepare stage.
all: PerfKitBenchmarker will run all above stages (prepare, run, cleanup). Any
resources
     generated in prepare will be automatically deleted at last.
     PerfKitBenchmarker won't
     be able to rerun with exactly same VMs, networks, disks with the same
     run_uri.
"""

import getpass
import logging
import sys
import uuid

from perfkitbenchmarker import benchmarks
from perfkitbenchmarker import benchmark_sets
from perfkitbenchmarker import benchmark_spec
from perfkitbenchmarker import flags
from perfkitbenchmarker import log_util
from perfkitbenchmarker import static_virtual_machine
from perfkitbenchmarker import timing_util
from perfkitbenchmarker import version
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.publisher import SampleCollector

STAGE_ALL = 'all'
STAGE_PREPARE = 'prepare'
STAGE_RUN = 'run'
STAGE_CLEANUP = 'cleanup'
REQUIRED_INFO = ['scratch_disk', 'num_machines']
# List of functions taking a benchmark_spec. Will be called before benchmark.Run
# with two parameters: the benchmark and benchmark_spec.
BEFORE_RUN_HOOKS = []
FLAGS = flags.FLAGS

flags.DEFINE_list('ssh_options', [], 'Additional options to pass to ssh.')
flags.DEFINE_integer('parallelism', 1,
                     'The number of benchmarks to run in parallel.')
flags.DEFINE_list('benchmarks', [benchmark_sets.STANDARD_SET],
                  'Benchmarks and/or benchmark sets that should be run. The '
                  'default is the standard set. For more information about '
                  'benchmarks and benchmark sets, see the README and '
                  'benchmark_sets.py.')
flags.DEFINE_string('project', None, 'GCP project ID under which '
                    'to create the virtual machines')
flags.DEFINE_list(
    'zones', None,
    'A list of zones within which to run PerfKitBenchmarker.'
    ' This is specific to the cloud provider you are running on. '
    'If multiple zones are given, PerfKitBenchmarker will create 1 VM in '
    'zone, until enough VMs are created as specified in each '
    'benchmark.')
# TODO(user): note that this is currently very GCE specific. Need to create a
#    module which can traslate from some generic types to provider specific
#    nomenclature.
flags.DEFINE_string('machine_type', None, 'Machine '
                    'types that will be created for benchmarks that don\'t '
                    'require a particular type.')
flags.DEFINE_integer('num_vms', 1, 'For benchmarks which can make use of a '
                     'variable number of machines, the number of VMs to use.')
flags.DEFINE_string('image', None, 'Default image that will be '
                    'linked to the VM')
flags.DEFINE_integer('scratch_disk_size', 500, 'Size, in gb, for all scratch '
                     'disks, default is 500')
flags.DEFINE_string('run_uri', None, 'Name of the Run. If provided, this '
                    'should be alphanumeric and less than or equal to 10 '
                    'characters in length.')
flags.DEFINE_string('owner', getpass.getuser(), 'Owner name. '
                    'Used to tag created resources and performance records.')
flags.DEFINE_enum(
    'log_level', log_util.INFO,
    [log_util.DEBUG, log_util.INFO],
    'The log level to run at.')
flags.DEFINE_enum(
    'run_stage', STAGE_ALL,
    [STAGE_ALL, STAGE_PREPARE, STAGE_RUN, STAGE_CLEANUP],
    'The stage of perfkitbenchmarker to run. By default it runs all stages.')
flags.DEFINE_list('benchmark_config_pair', None,
                  'Benchmark and its config file pair, separated by :.')
flags.DEFINE_integer('duration_in_seconds', None,
                     'duration of benchmarks. '
                     '(only valid for mesh_benchmark)')
flags.DEFINE_string('static_vm_file', None,
                    'The file path for the Static Machine file. See '
                    'static_virtual_machine.py for a description of this file.')
flags.DEFINE_boolean('version', False, 'Display the version and exit.')
flags.DEFINE_enum(
    'scratch_disk_type', benchmark_spec.STANDARD,
    [benchmark_spec.STANDARD, benchmark_spec.SSD, benchmark_spec.IOPS],
    'Type for all scratch disks. The default is standard')
flags.DEFINE_boolean('use_local_disk', False, 'For benchmarks that use disks, '
                     'this indicates that they should run against '
                     'the local disk(s) instead of remote storage.')
flags.DEFINE_integer('scratch_disk_iops', 1500,
                     'IOPS for Provisioned IOPS (SSD) volumes in AWS.')


MAX_RUN_URI_LENGTH = 10


# TODO(user): Consider moving to benchmark_spec.
def ValidateBenchmarkInfo(benchmark_info):
  for required_key in REQUIRED_INFO:
    if required_key not in benchmark_info:
      logging.error('Benchmark information %s is corrupt. It does not contain'
                    'the key %s. Please add the specified key to the benchmark'
                    'info. Skipping benchmark.', benchmark_info, required_key)
      # TODO(user): Raise error with info about the validation failure
      return False
  return True


def ListUnknownBenchmarks():
  """Identify invalid benchmark names specified in the command line flags."""
  valid_benchmark_names = frozenset(benchmark.GetInfo()['name']
                                    for benchmark in benchmarks.BENCHMARKS)
  valid_benchmark_sets = frozenset(benchmark_sets.BENCHMARK_SETS)
  specified_benchmark_names = frozenset(FLAGS.benchmarks)

  return sorted((specified_benchmark_names - valid_benchmark_names) -
                valid_benchmark_sets)


def DoPreparePhase(benchmark, name, spec, timer):
  """Performs the Prepare phase of benchmark execution.

  Args:
    benchmark: The benchmark module.
    name: A string containing the benchmark name.
    spec: The BenchmarkSpec created for the benchmark.
    timer: An IntervalTimer that measures the start and stop times of resource
      provisioning and the benchmark module's Prepare function.

  Returns:
    The BenchmarkSpec created for the benchmark.
  """
  logging.info('Preparing benchmark %s', name)
  with timer.Measure('Resource Provisioning'):
    spec.Prepare()
  with timer.Measure('Benchmark Prepare'):
    benchmark.Prepare(spec)


def DoRunPhase(benchmark, name, spec, collector, timer):
  """Performs the Run phase of benchmark execution.

  Args:
    benchmark: The benchmark module.
    name: A string containing the benchmark name.
    spec: The BenchmarkSpec created for the benchmark.
    collector: The SampleCollector object to add samples to.
    timer: An IntervalTimer that measures the start and stop times of the
      benchmark module's Run function.
  """
  logging.info('Running benchmark %s', name)
  for before_run_hook in BEFORE_RUN_HOOKS:
    before_run_hook(benchmark=benchmark, benchmark_spec=spec)
  with timer.Measure('Benchmark Run'):
    samples = benchmark.Run(spec)
  collector.AddSamples(samples, name, spec)


def DoCleanupPhase(benchmark, name, spec, timer):
  """Performs the Cleanup phase of benchmark execution.

  Args:
    benchmark: The benchmark module.
    name: A string containing the benchmark name.
    spec: The BenchmarkSpec created for the benchmark.
    timer: An IntervalTimer that measures the start and stop times of the
      benchmark module's Cleanup function and resource teardown.
  """
  logging.info('Cleaning up benchmark %s', name)
  with timer.Measure('Benchmark Cleanup'):
    benchmark.Cleanup(spec)
  with timer.Measure('Resource Teardown'):
    spec.Delete()


def RunBenchmark(benchmark, collector, sequence_number, total_benchmarks):
  """Runs a single benchmark and adds the results to the collector.

  Args:
    benchmark: The benchmark module to be run.
    collector: The SampleCollector object to add samples to.
    sequence_number: The sequence number of when the benchmark was started
      relative to the other benchmarks in the suite.
    total_benchmarks: The total number of benchmarks in the suite.
  """
  benchmark_info = benchmark.GetInfo()
  if not ValidateBenchmarkInfo(benchmark_info):
    return
  benchmark_name = benchmark_info['name']

  # Modify the logger prompt for messages logged within this function.
  label_extension = '{}({}/{})'.format(
      benchmark_name, sequence_number, total_benchmarks)
  log_context = log_util.GetThreadLogContext()
  with log_context.ExtendLabel(label_extension):
    # Optional prerequisite checking.
    check_prereqs = getattr(benchmark, 'CheckPrerequisites', None)
    if check_prereqs:
      try:
        check_prereqs()
      except:
        logging.exception('Prerequisite check failed for %s', benchmark_name)
        raise

    end_to_end_timer = timing_util.IntervalTimer()
    detailed_timer = timing_util.IntervalTimer()
    spec = None
    try:
      with end_to_end_timer.Measure('End to End'):
        if FLAGS.run_stage in [STAGE_ALL, STAGE_PREPARE]:
          # It is important to create the spec outside of DoPreparePhase
          # because if DoPreparePhase raises an exception, we still need
          # a reference to the spec in order to delete it in the "finally"
          # section below.
          spec = benchmark_spec.BenchmarkSpec(benchmark_info)
          # Pickle the spec before anything gets created so we can tear down
          # in a separate run if something goes wrong.
          spec.PickleSpec()
          DoPreparePhase(benchmark, benchmark_name, spec, detailed_timer)
        else:
          spec = benchmark_spec.BenchmarkSpec.GetSpecFromFile(benchmark_name)

        if FLAGS.run_stage in [STAGE_ALL, STAGE_RUN]:
          DoRunPhase(benchmark, benchmark_name, spec, collector, detailed_timer)

        if FLAGS.run_stage in [STAGE_ALL, STAGE_CLEANUP]:
          DoCleanupPhase(benchmark, benchmark_name, spec, detailed_timer)

      # Add samples for any timed interval that was measured.
      include_end_to_end = timing_util.EndToEndRuntimeMeasurementEnabled()
      include_runtimes = timing_util.RuntimeMeasurementsEnabled()
      include_timestamps = timing_util.TimestampMeasurementsEnabled()
      if FLAGS.run_stage == STAGE_ALL:
        collector.AddSamples(
            end_to_end_timer.GenerateSamples(
                include_runtime=include_end_to_end or include_runtimes,
                include_timestamps=include_timestamps),
            benchmark_name, spec)
      collector.AddSamples(
          detailed_timer.GenerateSamples(include_runtimes, include_timestamps),
          benchmark_name, spec)

    except Exception:
      # Resource cleanup (below) can take a long time. Log the error to give
      # immediate feedback, then re-throw.
      logging.exception('Error during benchmark %s', benchmark_name)
      raise
    finally:
      if FLAGS.run_stage in [STAGE_ALL, STAGE_CLEANUP]:
        if spec:
          spec.Delete()
      else:
        spec.PickleSpec()


def RunBenchmarks(publish=True):
  """Runs all benchmarks in PerfKitBenchmarker.

  Args:
    publish: A boolean indicating whether results should be published.

  Returns:
    Exit status for the process.
  """
  if FLAGS.version:
    print version.VERSION
    return

  if FLAGS.run_uri is None:
    if FLAGS.run_stage not in [STAGE_ALL, STAGE_PREPARE]:
      logging.error(
          'Cannot run "%s" with unspecified run_uri.', FLAGS.run_stage)
      return 1
    else:
      FLAGS.run_uri = str(uuid.uuid4())[-8:]
  elif not FLAGS.run_uri.isalnum() or len(FLAGS.run_uri) > MAX_RUN_URI_LENGTH:
    logging.error('run_uri must be alphanumeric and less than or equal '
                  'to 10 characters in length.')
    return 1

  vm_util.GenTempDir()
  log_util.ConfigureLogging(
      stderr_log_level=log_util.LOG_LEVELS[FLAGS.log_level],
      log_path=vm_util.PrependTempDir('pkb.log'),
      run_uri=FLAGS.run_uri)

  unknown_benchmarks = ListUnknownBenchmarks()
  if unknown_benchmarks:
    logging.error('Unknown benchmark(s) provided: %s',
                  ', '.join(unknown_benchmarks))
    return 1

  vm_util.SSHKeyGen()
  collector = SampleCollector()

  if FLAGS.static_vm_file:
    with open(FLAGS.static_vm_file) as fp:
      static_virtual_machine.StaticVirtualMachine.ReadStaticVirtualMachineFile(
          fp)

  if FLAGS.benchmark_config_pair:
    # Convert benchmark_config_pair into a {benchmark_name: file_name}
    # dictionary.
    tmp_dict = {}
    for config_pair in FLAGS.benchmark_config_pair:
      pair = config_pair.split(':')
      tmp_dict[pair[0]] = pair[1]
    FLAGS.benchmark_config_pair = tmp_dict

  try:
    benchmark_list = benchmark_sets.GetBenchmarksFromFlags()
    total_benchmarks = len(benchmark_list)
    if FLAGS.parallelism > 1:
      sequence_range = range(total_benchmarks, 0, -1)
      args = [((benchmark, collector, sequence_counter, total_benchmarks), {})
              for benchmark, sequence_counter
              in zip(benchmark_list, sequence_range)]
      vm_util.RunThreaded(
          RunBenchmark, args, max_concurrent_threads=FLAGS.parallelism)
    else:
      sequence_range = range(1, total_benchmarks + 1)
      for benchmark, sequence_counter in zip(benchmark_list, sequence_range):
        RunBenchmark(benchmark, collector, sequence_counter, total_benchmarks)
  finally:
    if collector.samples:
      collector.PublishSamples()

  if FLAGS.run_stage not in [STAGE_ALL, STAGE_CLEANUP]:
    logging.info(
        'To run again with this setup, please use --run_uri=%s', FLAGS.run_uri)


def Main(argv=sys.argv):
  logging.basicConfig(level=logging.INFO)
  # TODO: Verify if there is other way of appending additional help
  # message.
  # Inject more help documentation
  # The following appends descriptions of the benchmarks and descriptions of
  # the benchmark sets to the help text.
  benchmark_list = ['%s:  %s' % (benchmark_module.GetInfo()['name'],
                                 benchmark_module.GetInfo()['description'])
                    for benchmark_module in benchmarks.BENCHMARKS]
  benchmark_sets_list = [
      '%s:  %s' %
      (set_name, benchmark_sets.BENCHMARK_SETS[set_name]['message'])
      for set_name in benchmark_sets.BENCHMARK_SETS]
  sys.modules['__main__'].__doc__ = __doc__ + (
      '\nBenchmarks:\n\t%s') % '\n\t'.join(benchmark_list)
  sys.modules['__main__'].__doc__ += ('\n\nBenchmark Sets:\n\t%s'
                                      % '\n\t'.join(benchmark_sets_list))
  try:
    argv = FLAGS(argv)  # parse flags
  except flags.FlagsError as e:
    logging.error(
        '%s\nUsage: %s ARGS\n%s', e, sys.argv[0], FLAGS)
    sys.exit(1)
  return RunBenchmarks()
