# Copyright 2014 PerfKitBenchmarker Authors. All rights reserved.
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


"""Module containing OpenJDK7 installation and cleanup functions."""

JAVA_HOME = '/usr'
JAVA_SUSE11_REPO = 'http://download.opensuse.org/repositories/home:tdaitx/SLE_11_SP3/home:tdaitx.repo'

def YumInstall(vm):
  """Installs the OpenJDK7 package on the VM."""
  vm.InstallPackages('java-1.7.0-openjdk-devel')


def ZypperInstall(vm):
  """Installs the OpenJDK7 package on the VM."""
  if vm.GetSUSEVersion() >= 12:
    vm.InstallPackages('java-1.7.0-openjdk-devel')
  elif vm.GetSUSEVersion() == 11:
    vm.AddRepository(JAVA_SUSE11_REPO)
    vm.InstallPackages('java-1_7_0-openjdk')


def AptInstall(vm):
  """Installs the OpenJDK7 package on the VM."""
  vm.InstallPackages('openjdk-7-jdk')
