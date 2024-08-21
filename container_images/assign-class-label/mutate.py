import logging
import json
import base64
from flask import Flask, request, jsonify
from kubernetes import config, client
from openshift.dynamic import DynamicClient

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def get_client():
    try:
        config.load_config()
    except config.ConfigException as e:
        LOG.error('Could not configure Kubernetes client: %s', str(e))
        exit(1)

    k8s_client = client.ApiClient()
    dyn_client = DynamicClient(k8s_client)

    return dyn_client


# Get users of a given group
def get_group_members(group_resource, group_name):
    group_obj = group_resource.get(name=group_name)
    return group_obj.users


def assign_class_label(pod, groups, dyn_client):
    # Extract pod metadata
    pod_metadata = pod.get('metadata', {})
    pod_labels = pod_metadata.get('labels', {})
    pod_user = pod_labels.get('opendatahub.io/user', None)

    group_resource = dyn_client.resources.get(  # Need to move this group_resource, this is running every iteration of for loop
        api_version='user.openshift.io/v1', kind='Group'
    )

    # Iterate through classes
    for group in groups:
        users = get_group_members(group_resource, group)

        # Check if group has no users
        if not users:
            LOG.warning(f'Group {group} has no users or users attribute is not a list.')
            continue

        # Compare users in the groups (classes) with the pod user
        if pod_user in users:
            LOG.info(f'Assigning class label: {group} to user {pod_user}')
            return group

    return None


def create_app(**config):
    app = Flask(__name__)
    app.config.from_prefixed_env('RHOAI_CLASS')
    app.config.update(config)

    if not app.config['GROUPS']:
        LOG.error('RHOAI_CLASS_GROUPS environment variables are required.')
        exit(1)

    groups = app.config['GROUPS'].split(',')

    dyn_client = (
        get_client()
    )  # Moved here so not being called every for loop in assign_class_label

    @app.route('/mutate', methods=['POST'])
    def mutate_pod():
        # Grab pod for mutation
        request_info = request.get_json()
        uid = request_info['request']['uid']
        pod = request_info['request']['object']

        # Grab class that the pod user belongs to
        try:
            class_label = assign_class_label(pod, groups, dyn_client)
        except Exception as err:
            LOG.error('failed to assign class label: %s', err)
            return 'unexpected error encountered', 500, {'content-type': 'text/plain'}

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
        patch = [{'op': 'add', 'path': '/metadata/labels/class', 'value': class_label}]

        # Encode patch as base64 for response
        patch_base64 = base64.b64encode(json.dumps(patch).encode('utf-8')).decode(
            'utf-8'
        )
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

    return app
