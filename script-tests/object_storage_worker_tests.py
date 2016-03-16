# Copyright 2016 PerfKitBenchmarker Authors. All rights reserved.
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

"""Tests for the object_storage_service benchmark worker process."""

import itertools
import unittest

import object_storage_api_tests


class TestSizeDistributionIterator(unittest.TestCase):
  def testPointDistribution(self):
    dist = {}
    dist[10] = 100.0

    iter = object_storage_api_tests.SizeDistributionIterator(dist)

    lst = list(itertools.islice(iter, 5))

    self.assertEqual(lst, [10, 10, 10, 10, 10])


class TestMaxSizeInDistribution(unittest.TestCase):
  def testPointDistribution(self):
    dist = {}
    dist[10] = 100.0

    self.assertEqual(object_storage_api_tests.MaxSizeInDistribution(dist),
                     10)


if __name__ == '__main__':
  unittest.main()
