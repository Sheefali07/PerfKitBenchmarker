#!/usr/bin/env python

# Copyright 2017 PerfKitBenchmarker Authors. All rights reserved.
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
"""launch_mysql_service is a wrapper class for mysql_service_benchmark.
It will call mysql_service_benchmark with varying thread counts depending on
thread_count_list flag values.

If a run_uri flag is provided, the program assumes that the PKB instance
has been provisioned and prepared, and will only execute the run phase of PKB.
If no run_uri is given, the program will execute the provision and prepare
phase followed by consecutive run phases and then the cleanup and teardown
phase. Below are examples of possible ways to call this class.

Possible call:
./launch_mysql_service --thread_count_list=[1,2,5] --sysbench_run_seconds=20
./launch_mysql_service --run_uri=12e4s6t8 --thread_count_list=[1,2,4,8,10]

All requirements for mysql_service_benchmark still apply. See
perkfitbenchmarker.linuxbenchmarks.mysql_service_benchmark for more details.

Any additional flags not specifically outlined in the flags below can be added
as a list of strings under the 'additional_flags' flag. These flags will be
passed through as is to the underlying benchmarking code. For example,
a possible call with additional flags could be:
./launch_mysql_service --run_uri=2u2u2u3i
  --additional_flags=['--storage_size=100', '--cloud=GCP'].

Launcher has a few defaults, listed below:
  - Sysbench run seconds: 480
  - Sysbench warmup second: 0
  - Sysbench report interval (seconds): 2
  - Thread Count List: [1,2,4,8]
  - GCE VM Disk Size: 300GB
  - GCE VM Disk Type: pd-ssd
  - GCE VM Machine Type: n1-standard-16
"""

import datetime
import logging
import re
import shlex
import subprocess
import sys
import time
import gflags

import file_to_plot

# GLOBAL STRINGS
PER_SECOND_GRAPH_CLOUD_BUCKET = 'per_second_graph_cloud_bucket'
MYSQL_SVC_DB_INSTANCE_CORES = 'mysql_svc_db_instance_cores'
MYSQL_SVC_OLTP_TABLES_COUNT = 'mysql_svc_oltp_tables_count'
MYSQL_SVC_OLTP_TABLE_SIZE = 'mysql_svc_oltp_table_size'
SYSBENCH_WARMUP_SECONDS = 'sysbench_warmup_seconds'
SYSBENCH_RUN_SECONDS = 'sysbench_run_seconds'
SYSBENCH_THREAD_COUNT = 'sysbench_thread_count'
SYSBENCH_REPORT_INTERVAL = 'sysbench_report_interval'
THREAD_COUNT_LIST = 'thread_count_list'
GCE_BOOT_DISK_SIZE = 'gce_boot_disk_size'
GCE_BOOT_DISK_TYPE = 'gce_boot_disk_type'
MACHINE_TYPE = 'machine_type'
RUN_URI = 'run_uri'
RUN_STAGE = 'run_stage'
STDOUT = 'STDOUT'
STDERR = 'STDERR'
DATETIME_FORMAT = '{:%m_%d_%Y_%H_%M_}'
URI_REGEX = r'run_uri=([a-z0-9]{8})'
ADDITIONAL_FLAGS = 'additional_flags'
SLEEP_TIME_BETWEEN_RUNS = 20  # seconds
TAIL_LINE_NUM = '20'

PKB_TIMEOUT = 43200  # max wait time for a run in seconds
TIME_MIN = 1

# FLAG STRINGS
PKB = './pkb.py --benchmarks=mysql_service'
STAGE_FLAG = ' --run_stage='
URI_FLAG = ' --run_uri='
THREAD_FLAG = ' --sysbench_thread_count='
RUN_TIME = ' --sysbench_run_seconds='
WARMUP_FLAG = ' --sysbench_warmup_seconds='
BOOT_DISK_SIZE_FLAG = ' --gce_boot_disk_size='
BOOT_DISK_TYPE_FLAG = ' --gce_boot_disk_type='
MACHINE_TYPE_FLAG = ' --machine_type='
MYSQL_SVC_DB_CORES_FLAG = ' --mysql_svc_db_instance_cores='
MYSQL_SVC_DB_TABLES_COUNT_FLAG = ' --mysql_svc_oltp_tables_count='
MYSQL_SVC_OLTP_TABLE_SIZE_FLAG = ' --mysql_svc_oltp_table_size='

PROVISION = 'provision'
PREPARE = 'prepare'
RUN = 'run'
CLEANUP = 'cleanup'
TEARDOWN = 'teardown'

FLAGS = gflags.FLAGS
gflags.DEFINE_string(PER_SECOND_GRAPH_CLOUD_BUCKET, None,
                     'Indicator for using per second data collection.'
                     'To enable set the google cloud storage bucket for the '
                     'graph.')
gflags.DEFINE_integer(SYSBENCH_RUN_SECONDS, 480,
                      'The duration, in seconds, of each run phase with varying'
                      'thread count.')
gflags.DEFINE_integer(SYSBENCH_WARMUP_SECONDS, 0,
                      'The duration, in seconds, of the warmup run in which '
                      'results are discarded.')
gflags.DEFINE_list(THREAD_COUNT_LIST, [1, 2, 4, 8],
                   'The number of test threads on the client side.')
gflags.DEFINE_integer(SYSBENCH_REPORT_INTERVAL, 2,
                      'The interval, in seconds, we ask sysbench to report '
                      'results.')
gflags.DEFINE_string(RUN_URI, None,
                     'Run identifier, if provided, only run phase '
                     'will be completed.')
gflags.DEFINE_string(RUN_STAGE, None,
                     'List of phases to be executed. For example:'
                     '"--run_uri=provision,prepare". Available phases:'
                     'prepare, provision, run, cleanup, teardown.')
gflags.DEFINE_string(GCE_BOOT_DISK_SIZE, '300',
                     'The boot disk size in GB for GCP VMs..')
gflags.DEFINE_string(GCE_BOOT_DISK_TYPE, 'pd-ssd',
                     'The boot disk type for GCP VMs.')
gflags.DEFINE_string(MACHINE_TYPE, 'n1-standard-4',
                     'Machine type for GCE Virtual machines.')
gflags.DEFINE_enum(MYSQL_SVC_DB_INSTANCE_CORES, '4', ['1', '4', '8', '16'],
                   'The number of cores to be provisioned for the DB instance.')
gflags.DEFINE_integer(MYSQL_SVC_OLTP_TABLES_COUNT, 4,
                      'The number of tables used in sysbench oltp.lua tests')
gflags.DEFINE_integer(MYSQL_SVC_OLTP_TABLE_SIZE, 100000,
                      'The number of rows of each table used in the oltp tests')
gflags.DEFINE_list(ADDITIONAL_FLAGS, None,
                   'List of additional PKB mysql_service valid flags (strings).'
                   'For example: "--storage_size=100","--cloud_storage_bucket='
                   'bucket_name".')

# TODO: Implement flag for STDOUT/STDERR file paths.


class UnexpectedFileOutputError(Exception):
  pass


class OperationTimeoutError(Exception):
  pass


class CallFailureError(Exception):
  pass


def driver(argv):
  """Driver initiates sysbench run with different thread counts.

  If running this wrapper module with a bash script the print statement
  can be used to capture the run_uri. This allows user to provision and prepare
  the database and client VM less frequently which is advantageous when the
  specifications contain larger values.

  Args:
    argv: system arguments (command line flags).
  """
  try:  # Parse command line flags
    argv = FLAGS(argv)
  except gflags.FlagsError as e:
    logging.error('%s\nUsage: %s ARGS\n%s', e, sys.argv[0], FLAGS)
    sys.exit(1)
  run_uri = FLAGS.run_uri
  run_stage = FLAGS.run_stage
  if not run_uri:
    if not run_stage:
      logging.info('No run_uri given. Will run full mysql_service_benchmark '
                   'test.')
    run_uri = _provision_prepare_pkb()
    logging.info('Provision and prepare completed. Run uri assigned: %s',
                 run_uri)
    if run_stage == 'provision,prepare':
      print run_uri
      return run_uri
  if not run_stage or run_stage == RUN:
    _run(run_uri)
  if not run_stage or run_stage == 'cleanup,teardown':
    _cleanup_teardown_pkb(run_uri)
  print(run_uri)
  return run_uri


def _provision_prepare_pkb():
  """Run provision and prepare stage of PKB benchmark.

  Returns:
    run_uri: (string)
  """
  pkb_cmd = PKB + STAGE_FLAG + PROVISION + ',' + PREPARE
  pkb_cmd += (
      BOOT_DISK_SIZE_FLAG + FLAGS.gce_boot_disk_size + BOOT_DISK_TYPE_FLAG +
      FLAGS.gce_boot_disk_type + MACHINE_TYPE_FLAG + FLAGS.machine_type +
      MYSQL_SVC_DB_CORES_FLAG + FLAGS.mysql_svc_db_instance_cores +
      MYSQL_SVC_DB_TABLES_COUNT_FLAG + str(FLAGS.mysql_svc_oltp_tables_count) +
      MYSQL_SVC_OLTP_TABLE_SIZE_FLAG + str(FLAGS.mysql_svc_oltp_table_size))
  if FLAGS.additional_flags:
    pkb_cmd = _append_additional_flags(pkb_cmd)
  # PKB run with prepare,provision, wait
  logging.info('Provision and prepare sysbench with the following command:\n%s',
               pkb_cmd)
  [stdout_filename, stderr_filename] = _generate_filenames(PROVISION, None)
  _execute_pkb_cmd(pkb_cmd, stdout_filename, stderr_filename)
  return _get_run_uri(stderr_filename)


def _run(run_uri):
  """Run stage of PKB benchmark.

  Args:
    run_uri: (string).
  """
  if FLAGS.per_second_graph_cloud_bucket:
    plotter = file_to_plot.Plotter(FLAGS.sysbench_run_seconds,
                                   FLAGS.report_interval,
                                   FLAGS.per_second_graph_cloud_bucket)
  run_iterations = len(FLAGS.thread_count_list)
  logging.info(
      'Beginning run phase. Will execute runs with %d different thread counts.',
      run_iterations)
  for t in FLAGS.thread_count_list:
    pkb_cmd = (PKB + STAGE_FLAG + RUN + URI_FLAG + run_uri + THREAD_FLAG +
               str(t) + RUN_TIME + str(FLAGS.sysbench_run_seconds) + WARMUP_FLAG
               + str(FLAGS.sysbench_warmup_seconds))
    if FLAGS.additional_flags:
      pkb_cmd = _append_additional_flags(pkb_cmd)
    stdout_filename, stderr_filename = _generate_filenames(RUN, t)
    logging.info('Executing PKB run with thread count: %s', t)
    logging.info('Run sysbench with the following command:\n%s', pkb_cmd)
    _execute_pkb_cmd(pkb_cmd, stdout_filename, stderr_filename)
    if FLAGS.per_second_data:
      plotter.add_file(stderr_filename)
    logging.info('Finished executing PKB run.')
    time.sleep(SLEEP_TIME_BETWEEN_RUNS)
  if FLAGS.per_second_graph_cloud_bucket:
    plotter.plot()


def _cleanup_teardown_pkb(run_uri):
  """Run cleanup stage of PKB benchmark.

  Args:
    run_uri: (string)
  """
  logging.info('Run phase complete. Starting cleanup/teardown.')
  pkb_cmd = (PKB + STAGE_FLAG + CLEANUP + ',' + TEARDOWN + URI_FLAG + run_uri)
  logging.info('Cleanup, teardown sysbench with the following command:'
               '\n%s', pkb_cmd)
  [stdout_filename, stderr_filename] = _generate_filenames(CLEANUP, None)
  _execute_pkb_cmd(pkb_cmd, stdout_filename, stderr_filename)
  logging.info('Finished executing PKB cleanup and teardown.')


def _execute_pkb_cmd(pkb_cmd, stdout_filename, stderr_filename):
  """Given pkb run command, execute.

  Args:
    pkb_cmd: (str)
    stdout_filename: (str) filename string.
    stderr_filename: (str) filename_str
  """
  stdout_file = open(stdout_filename, 'w+')
  stderr_file = open(stderr_filename, 'w+')
  pkb_cmd_list = shlex.split(pkb_cmd)
  logging.info('pkb command list: %s', str(pkb_cmd_list))
  start_time = time.time()
  p = subprocess.Popen(pkb_cmd_list, stdout=stdout_file, stderr=stderr_file)
  logging.info('Waiting for PKB call to finish.')
  # TODO: implement timeout. Currently this call will wait unbounded.
  # Will probably have to implement with threading.
  p.wait()
  elapsed_time = time.time() - start_time
  if elapsed_time == 1:
    raise CallFailureError('The call failed before execution (duration 1s). '
                           'Check stderr for traceback.')
  logging.info('PKB call finished in %i seconds.', int(elapsed_time))


def _get_run_uri(filename):
  """Grab the last lines of file and return the first match with URI_REGEX.

  Args:
    filename: (string)

  Returns:
    run_uri: (string) Run identifier from file.

  Raises:
    Exception: No match with regular expression. Unexpected output to filename.
  """
  grab_file_tail_cmd = ['tail', '-n', TAIL_LINE_NUM, filename]
  p = subprocess.Popen(
      grab_file_tail_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  lines = p.stdout.readlines()
  r = re.compile(URI_REGEX)
  for line in lines:
    matches = r.search(line)
    if matches:
      return matches.group(matches.lastindex)
  raise UnexpectedFileOutputError('No regex match with %s.', filename)


def _append_additional_flags(pkb_cmd):
  """Appends additional flags to the end of pkb_cmd.

  Args:
    pkb_cmd: (string) Current pkb command.

  Returns:
    pkb_cmd: (string) PKB command with additional flags.
  """
  for flag in FLAGS.additional_flags:
    pkb_cmd += ' ' + flag
  return pkb_cmd


def _generate_filenames(run_stage, thread_number):
  """Generate filenames for STDOUT and STDERR based on phase and time.

  Args:
    run_stage: Current stage of sysbench.
    thread_number: (int) Number of sysbench threads for run iteration.

  Returns:
    [stdout_filename, stderr_filename]: list of filename strings.
  """
  date_string = DATETIME_FORMAT.format(datetime.datetime.now())
  if run_stage == RUN:
    stdout_filename = date_string + str(
        thread_number) + '_THREAD_RUN' + '_PKB_STDOUT.txt'
    stderr_filename = date_string + str(
        thread_number) + '_THREAD_RUN' + '_PKB_STDERR.txt'
  else:
    stdout_filename = date_string + str(run_stage) + '_PKB_STDOUT.txt'
    stderr_filename = date_string + str(run_stage) + '_PKB_STDERR.txt'
  logging.info('STDOUT will be copied to: %s', stdout_filename)
  logging.info('STDERR will be copied to: %s', stderr_filename)
  return [stdout_filename, stderr_filename]


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  driver(sys.argv)
