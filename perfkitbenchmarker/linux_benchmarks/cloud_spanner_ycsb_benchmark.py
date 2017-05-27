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

"""Run YCSB benchmark against Google Cloud Spanner

Before running this benchmark, you have to download your JSON
service account credential file to local machine, and pass the path
via 'cloud_spanner_credential_file' parameters to PKB.

By default, this benchmark provision 1 single-CPU VM and spawn 1 thread
to test Spanner.
"""

import getpass
import logging

from perfkitbenchmarker import configs
from perfkitbenchmarker import flags
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_packages import ycsb
from perfkitbenchmarker.providers.gcp import gcp_spanner

BENCHMARK_NAME = 'cloud_spanner_ycsb'
BENCHMARK_CONFIG = """
cloud_spanner_ycsb:
  description: >
      Run YCSB against Google Cloud Spanner.
      Configure the number of VMs via --num-vms.
  vm_groups:
    default:
      vm_spec: *default_single_core
      vm_count: 1
  flags:
    gcloud_scopes: >
      https://www.googleapis.com/auth/spanner.admin
      https://www.googleapis.com/auth/spanner.data"""

# As of May 2017, cloud spanner is not included in the latest YCSB release
# 0.12.0. You can build your own release binary and feed it in with
# --cloud_spanner_custom_ycsb_release=<url>. You are welcome to use a pre-build
# binary located at
# https://storage.googleapis.com/cloud-spanner-ycsb-custom-release/
# ycsb-cloudspanner-binding-0.13.0.tar.gz
YCSB_BINDING_TAR_URL = ('https://github.com/brianfrankcooper/YCSB/releases'
                        '/download/0.13.0/'
                        'ycsb-cloudspanner-binding-0.13.0.tar.gz')
REQUIRED_SCOPES = (
    'https://www.googleapis.com/auth/spanner.admin',
    'https://www.googleapis.com/auth/spanner.data')

FLAGS = flags.FLAGS
flags.DEFINE_string('table',
                    'usertable',
                    'The table name used in the YCSB benchmark.')
flags.DEFINE_integer('zeropadding',
                     12,
                     'The zero padding used in the YCSB benchmark.')
flags.DEFINE_string('cloud_spanner_host',
                    None,
                    'The Cloud Spanner host used in the YCSB benchmark.')
flags.DEFINE_string('cloud_spanner_instance',
                    'ycsb-' + getpass.getuser(),
                    'The Cloud Spanner instance used in the YCSB benchmark.')
flags.DEFINE_string('cloud_spanner_database',
                    'ycsb',
                    'The Cloud Spanner database used in the YCSB benchmark.')
flags.DEFINE_string('cloud_spanner_readmode',
                    'query',
                    'The Cloud Spanner read mode used in the YCSB benchmark.')
flags.DEFINE_integer('cloud_spanner_boundedstaleness',
                     0,
                     'The Cloud Spanner bounded staleness used in the YCSB '
                     'benchmark.')
flags.DEFINE_integer('cloud_spanner_batchinserts',
                     1,
                     'The Cloud Spanner batch inserts used in the YCSB '
                     'benchmark.')
flags.DEFINE_string('cloud_spanner_custom_ycsb_release',
                    None,
                    'If provided, the URL of a custom YCSB release')
flags.DEFINE_string('cloud_spanner_desp',
                    'YCSB',
                    'The description of the Cloud Spanner instance.')
flags.DEFINE_integer('cloud_spanner_nodes',
                     1,
                     'The number of nodes for the Cloud Spanner instance.')
flags.DEFINE_string('cloud_spanner_config',
                    'regional-us-central1',
                    'The config for the Cloud Spanner instance.')
flags.DEFINE_string('cloud_spanner_ddl',
                    """CREATE TABLE usertable (
                    id STRING(MAX), field0 STRING(MAX), field1 STRING(MAX),
                    field2 STRING(MAX), field3 STRING(MAX), field4 STRING(MAX),
                    field5 STRING(MAX), field6 STRING(MAX), field7 STRING(MAX),
                    field8 STRING(MAX), field9 STRING(MAX),
                    ) PRIMARY KEY(id)""",
                    'The schema DDL for the Cloud Spanner database.')


def GetConfig(user_config):
  config = configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)
  if FLAGS['ycsb_client_vms'].present:
    config['vm_groups']['default']['vm_count'] = FLAGS.ycsb_client_vms
  return config


def CheckPrerequisites(benchmark_config):
  for scope in REQUIRED_SCOPES:
    if scope not in FLAGS.gcloud_scopes:
      raise ValueError('Scope {0} required.'.format(scope))


def Prepare(benchmark_spec):
  benchmark_spec.always_call_cleanup = True

  benchmark_spec.spanner_instance = gcp_spanner.GcpSpannerInstance(
      FLAGS.cloud_spanner_instance, FLAGS.cloud_spanner_desp,
      FLAGS.cloud_spanner_nodes, FLAGS.cloud_spanner_config,
      FLAGS.cloud_spanner_database, FLAGS.cloud_spanner_ddl)
  benchmark_spec.spanner_instance.Delete()
  benchmark_spec.spanner_instance.Create()
  if not benchmark_spec.spanner_instance._Exists():
    logging.warning('Failed to create Cloud Spanner instance and database.')
    benchmark_spec.spanner_instance.Delete()

  default_ycsb_tar_url = ycsb.YCSB_TAR_URL

  # TODO: figure out a less hacky way to override.
  # Override so that we only need to download the required binding.
  if FLAGS.cloud_spanner_custom_ycsb_release:
    ycsb.YCSB_TAR_URL = FLAGS.cloud_spanner_custom_ycsb_release
  else:
    ycsb.YCSB_TAR_URL = YCSB_BINDING_TAR_URL

  logging.warning('YCSB tar url: ' + ycsb.YCSB_TAR_URL)

  vms = benchmark_spec.vms

  # Install required packages and copy credential files
  vm_util.RunThreaded(_Install, vms)

  # Restore YCSB_TAR_URL
  ycsb.YCSB_TAR_URL = default_ycsb_tar_url
  benchmark_spec.executor = ycsb.YCSBExecutor('cloudspanner')


def Run(benchmark_spec):
  vms = benchmark_spec.vms
  run_kwargs = {
      'table': FLAGS.table,
      'zeropadding': FLAGS.zeropadding,
      'cloudspanner.instance': FLAGS.cloud_spanner_instance,
      'cloudspanner.database': FLAGS.cloud_spanner_database,
      'cloudspanner.readmode': FLAGS.cloud_spanner_readmode,
      'cloudspanner.boundedstaleness': FLAGS.cloud_spanner_boundedstaleness,
      'cloudspanner.batchinserts': FLAGS.cloud_spanner_batchinserts,
  }
  if FLAGS.cloud_spanner_host:
    run_kwargs['cloudspanner.host'] = FLAGS.cloud_spanner_host

  load_kwargs = run_kwargs.copy()
  if FLAGS['ycsb_preload_threads'].present:
    load_kwargs['threads'] = FLAGS.ycsb_preload_threads
  samples = list(benchmark_spec.executor.LoadAndRun(
      vms, load_kwargs=load_kwargs, run_kwargs=run_kwargs))
  return samples


def Cleanup(benchmark_spec):
  benchmark_spec.spanner_instance.Delete()


def _Install(vm):
  vm.Install('ycsb')
