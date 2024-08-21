import logging
import json
import base64
import os
from flask import Flask, request, jsonify
from kubernetes import config, client
from openshift.dynamic import DynamicClient

webhook = Flask(__name__)

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

try:
    config.load_incluster_config()
except config.ConfigException:
    try:
        config.load_kube_config()
    except config.ConfigException:
        LOG.error('Could not configure Kubernetes client')
        exit(1)

k8s_client = client.ApiClient()
dyn_client = DynamicClient(k8s_client)


# Get list of classes (groups)
groups_env = os.environ.get('CLASS_GROUPS')

if not groups_env:
    LOG.error('Must set CLASS_GROUPS environment variable in deployment.yaml')
    exit(1)

groups = groups_env.split(',')


def assign_class_label(pod):
    # Extract pod metadata
    pod_metadata = pod.get('metadata', {})
    pod_labels = pod_metadata.get('labels', {})
    pod_user = pod_labels.get('opendatahub.io/user', None)

    if not pod_user:
        return None

    # Create group api client
    group_resource = dyn_client.resources.get(
        api_version='user.openshift.io/v1', kind='Group'
    )

    # Iterate through classes
    for group in groups:
        try:
            # Get users in class (group)
            group_obj = group_resource.get(name=group)
            group_users = group_obj.users

            # Check if group has no users
            if not group_users:
                LOG.warning(
                    f'Group {group} has no users or users attribute is not a list.'
                )
                continue

            # Compare users in the groups (classes) with the pod user
            if pod_user in group_users:
                LOG.info(f'Assigning class label: {group} to user {pod_user}')
                return group
        except Exception as e:
            LOG.error(f'Error fetching group {group}: {str(e)}')
            continue

    return None


@webhook.route('/mutate', methods=['POST'])
def mutate_pod():
    # Grab pod for mutation
    request_info = request.get_json()
    uid = request_info['request']['uid']
    pod = request_info['request']['object']

    # Grab class that the pod user belongs to
    class_label = assign_class_label(pod)

    # If user not in any class, return without modifications
    if not class_label:
        return jsonify(
            {
                'apiVersion': 'admission.k8s.io/v1',
                'kind': 'AdmissionReview',
                'response': {
                    'uid': uid,
                    'allowed': True,
                    'status': {'message': 'No class label assigned.'},
                },
            }
        )

    # Generate JSON Patch to add class label
    patch = [
        {'op': 'add', 'path': "/metadata/labels/ope_class", 'value': class_label}
    ]

    # Encode patch as base64 for response
    patch_base64 = base64.b64encode(json.dumps(patch).encode('utf-8')).decode('utf-8')

    # Return webhook response that includes the patch to add class label
    return jsonify(
        {
            'apiVersion': 'admission.k8s.io/v1',
            'kind': 'AdmissionReview',
            'response': {
                'uid': uid,
                'allowed': True,
                'patchType': 'JSONPatch',
                'patch': patch_base64,
            },
        }
    )
