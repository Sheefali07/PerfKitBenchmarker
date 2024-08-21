"""Implementation of a model using Jumpstart on AWS Sagemaker.

Uses amazon python library to deploy & manage that model.

One time step:
- Create an Sagemaker execution ARN for your account. See here for details:
https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-geospatial-roles-create-execution-role.html
"""

import json
import logging
import re
from absl import flags
from perfkitbenchmarker import errors
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.providers.aws import util
from perfkitbenchmarker.resources import managed_ai_model
from perfkitbenchmarker.resources import managed_ai_model_spec

FLAGS = flags.FLAGS


EXECUTION_ARN_BASE = 'arn:aws:iam::{account_number}:role/sagemaker-full-access'

_CREATE_MODEL_PYTHON = """
from sagemaker.jumpstart.model import JumpStartModel
model = JumpStartModel(
    model_id='{model_id}',
    model_version='{model_version}',
    region='{region}',
    role='{role}',
)
predictor = model.deploy()
print("Endpoint name: <" + predictor.endpoint_name + ">")
"""

_GET_MODEL_PYTHON = """
from sagemaker import predictor as predictor_lib
predictor = predictor_lib.retrieve_default('{endpoint_name}',region='{region}',model_id='{model_id}',model_version='{model_version}')
"""

# Must be combined with GET_MODEL above.
_SEND_REQUEST_PYTHON = """
def print_dialog(payload, response):
    dialog = payload["inputs"][0]
    for msg in dialog:
        print(f"{msg['role'].capitalize()}: {msg['content']}\n")
    print(
        f">>>> {response[0]['generation']['role'].capitalize()}: {response[0]['generation']['content']}"
    )
    print("\n==================================\n")

payload = {
    "inputs": [
        [
            {"role": "user", "content": "what is the recipe of mayonnaise?"},
        ]
    ],
    "parameters": {"max_new_tokens": 512, "top_p": 0.9, "temperature": 0.6},
}
try:
    response = predictor.predict(payload, custom_attributes="accept_eula=true")
    print_dialog(payload, response)
except Exception as e:
    print(e)

"""

# Must be combined with GET_MODEL above.
_DELETE_MODEL_PYTHON = """
predictor.delete_model()
predictor.delete_endpoint()
"""


class JumpStartModelInRegistry(managed_ai_model.BaseManagedAiModel):
  """Represents a Vertex AI model in the model registry.

  Attributes:
    model_name: The official name of the model in Model Garden, e.g. Llama2.
    name: The name of the created model in private model registry.
    account_id: The AWS account id.
    endpoint_name: The name of the deployed endpoint, if initialized.
    execution_arn: The role the model uses to run.
  """

  CLOUD = 'AWS'

  model_spec: 'JumpStartModelSpec'
  model_name: str
  model_id: str
  model_version: str
  name: str
  account_id: str
  endpoint_name: str | None
  execution_arn: str

  def __init__(
      self, model_spec: managed_ai_model_spec.BaseManagedAiModelSpec, **kwargs
  ):
    super().__init__(**kwargs)
    if not isinstance(model_spec, JumpStartModelSpec):
      raise errors.Config.InvalidValue(
          f'Invalid model spec class: "{model_spec.__class__.__name__}". '
          'Must be a JumpStartModelSpec. It had config values of '
          f'{model_spec.model_name} & {model_spec.cloud}'
      )
    self.model_spec = model_spec
    self.model_name = model_spec.model_name
    self.model_id = model_spec.model_id
    self.model_version = model_spec.model_version
    self.account_id = util.GetAccount()
    self.execution_arn = EXECUTION_ARN_BASE.format(
        account_number=self.account_id
    )
    self.endpoint_name = None
    self.metadata.update({
        'model_name': self.model_name,
    })

  def GetRegionFromZone(self, zone: str) -> str:
    return util.GetRegionFromZone(zone)

  def ListExistingEndpoints(self, region: str | None = None) -> list[str]:
    """Returns list of endpoint names."""
    if region is None:
      region = self.region
    out, _, _ = vm_util.IssueCommand(
        ['aws', 'sagemaker', 'list-endpoints', f'--region={region}']
    )
    out_json = json.loads(out)
    json_endpoints = out_json['Endpoints']
    endpoints = []
    for json_endpoint in json_endpoints:
      endpoints.append(json_endpoint['EndpointName'])
    return endpoints

  def _RunPythonCode(self, python_code: str) -> tuple[str, str]:
    """Runs the given python code on the Runner VM.

    We do this rather than just run the python code in this file to avoid
    importing the AWS libraries.
    Args:
      python_code: The python code to run.

    Returns:
      Tuple of [stdout, stderr].
    """
    # TODO(user): Instead call a python script with flags.
    with vm_util.NamedTemporaryFile(mode='w', delete=False) as tf:
      tf.write(python_code)
      tf.close()
      name = tf.name
      vm_util.IssueCommand(['cat', name])
      out, err, _ = vm_util.IssueCommand(
          ['python3', name], raise_on_failure=False, timeout=60 * 30
      )
      return out, err

  def _Create(self) -> None:
    """Creates the underlying resource."""
    logging.info('Creating Jump Start Model: %s', self.model_id)
    out, err = self._RunPythonCode(
        _CREATE_MODEL_PYTHON.format(
            model_id=self.model_id,
            model_version=self.model_version,
            region=self.region,
            role=self.execution_arn,
        )
    )
    # TODO(user): Handle errors rather than swallowing them.
    # Unfortunately even a correct run gives some errors.
    matches = re.search('Endpoint name: <(.+?)>', out)
    if not matches:
      raise errors.Resource.CreationError(
          'Could not find endpoint name in python create output.\nStdout:'
          f' {out}\nStderr:{err}',
      )
    self.endpoint_name = matches.group(1)

  def _CreateDependencies(self) -> None:
    vm_util.IssueCommand(['pip', 'install', 'sagemaker'])

  def _Delete(self) -> None:
    """Deletes the underlying resource."""
    assert self.endpoint_name
    self._RunPythonCode(
        _GET_MODEL_PYTHON.format(
            endpoint_name=self.endpoint_name,
            model_id=self.model_id,
            model_version=self.model_version,
            region=self.region,
            role=self.execution_arn,
        )
        + _DELETE_MODEL_PYTHON
    )


class JumpStartModelSpec(managed_ai_model_spec.BaseManagedAiModelSpec):
  """Spec for a Sagemaker JumpStart model.

  Attributes:
    model_id: Id of the model .
    model_version: Version of the model.
  """

  CLOUD = 'AWS'

  def __init__(self, component_full_name, flag_values=None, **kwargs):
    super().__init__(component_full_name, flag_values=flag_values, **kwargs)
    self.model_id: str
    self.model_version: str


class JumpStartLlama27bSpec(JumpStartModelSpec):
  """Spec for running the Llama2 7b model.

  Source is this python notebook:
  https://github.com/aws/amazon-sagemaker-examples/blob/main/introduction_to_amazon_algorithms/jumpstart-foundation-models/llama-2-text-completion.ipynb
  """

  MODEL_NAME = 'llama2_7b'

  def __init__(self, component_full_name, flag_values=None, **kwargs):
    super().__init__(component_full_name, flag_values=flag_values, **kwargs)
    self.model_id = 'meta-textgeneration-llama-2-7b-f'
    self.model_version = '2.*'