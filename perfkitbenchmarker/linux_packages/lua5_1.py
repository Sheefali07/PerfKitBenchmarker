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


"""Module containing lua installation and cleanup functions."""


def YumInstall(vm):
  """Installs lua on the VM."""
  vm.InstallPackages('lua lua-devel lua-static')


def AptInstall(vm):
  """Installs lua on the VM."""
  vm.InstallPackages('lua5.1 liblua5.1-dev')


def ZypperInstall(vm):
  """Installs lua on the VM."""
  if vm.GetSUSEVersion() >= 12:
    vm.InstallPackages('lua51 lua51-devel')
  elif vm.GetSUSEVersion() == 11:
    vm.InstallPackages('lua lua-devel')
