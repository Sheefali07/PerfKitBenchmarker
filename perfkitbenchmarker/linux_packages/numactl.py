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

"""Module containing numactl installation and cleanup functions."""


def _Install(vm):
  """Installs the numactl package on the VM."""
  vm.InstallPackages('numactl')


def ZypperInstall(vm):
  """Installs the numactl package on the VM."""
  _Install(vm)


def YumInstall(vm):
  """Installs the numactl package on the VM."""
  _Install(vm)


def AptInstall(vm):
  """Installs the numactl package on the VM."""
  _Install(vm)
