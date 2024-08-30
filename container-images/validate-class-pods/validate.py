import logging
import json
import base64
from flask import Flask, request, jsonify, Response
from kubernetes import config, client
from openshift.dynamic import DynamicClient

from pydantic import BaseModel, ValidationError, constr
from typing import Dict, Any, Optional, List

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class PodSpec(BaseModel):
    containers: Optional[List[Dict[str, Any]]] = None

class PodMetadata(BaseModel):
    labels: Optional[Dict[str, str]] = None

class PodObject(BaseModel):
    metadata: PodMetadata
    spec: Optional[PodSpec] = None

class AdmissionRequest(BaseModel):
    uid: constr(min_length=1)
    object: PodObject

class AdmissionReview(BaseModel):
    request: AdmissionRequest


def decode_pod_user(pod_user: str) -> str:
    user = pod_user.replace('-40', '@').replace('-2e', '.')
    return user

def get_client() -> DynamicClient:
    try:
        config.load_config()
        k8s_client = client.ApiClient()
        dyn_client = DynamicClient(k8s_client)
        return dyn_client
    except config.ConfigException as e:
        LOG.error('Could not configure Kubernetes client: %s', str(e))
        exit(1)


def create_app(**config: Any) -> Flask:
    app = Flask(__name__)
    app.config.from_prefixed_env('CLASS')
    app.config.update(config)

    if not app.config['IMAGE']:
        LOG.error('RHOAI_CLASS_GROUPS environment variables are required.')
        exit(1)

    allowed_image = app.config['IMAGE']

    @app.route('/validate', methods=['POST'])
    def validate_pod() -> Response:
        # Grab pod for mutation and validate request
        try:
            admission_review = AdmissionReview(**request.get_json())
        except ValidationError as e:
            LOG.error('Validation error: %s', e)
            return (
                jsonify(
                    {
                        'apiVersion': 'admission.k8s.io/v1',
                        'kind': 'AdmissionReview',
                        'response': {
                            'uid': request.json.get('request', {}).get('uid', ''),
                            'allowed': False,
                            'status': {'message': f'Invalid request: {e}'},
                        },
                    }
                ),
                400,
                {'content-type': 'application/json'},
            )
        
        pod = admission_review.request.object.model_dump()

        # Extract pod metadata
        try:
            pod_metadata = pod.get('metadata', {})
            pod_labels = pod_metadata.get('labels', {})
            pod_user = pod_labels.get('opendatahub.io/user', None)
        except AttributeError as e:
            LOG.error(f'Error extracting pod information: {e}')
            return None
        
        
        if pod_user is not None:
            pod_user = decode_pod_user(pod_user)
            container_name = f"jupyter-nb-{pod_user}"
            LOG.info(container_name)


            for container in pod.get('spec', {}).get('containers', []):
                if container.get('name') == container_name:
                    if container.get('image') != allowed_image:

                        # Deny request if not correct image
                        return (
                            jsonify(
                                {
                                    'apiVersion': 'admission.k8s.io/v1',
                                    'kind': 'AdmissionReview',
                                    'response': {
                                        'uid': admission_review.request.uid,
                                        'allowed': False,
                                        'status': {
                                            'message': f'Invalid Jupyter image: {container.get("image")}. Must use {allowed_image}.',
                                        },
                                    },
                                }
                            ),
                            200,
                            {'content-type': 'application/json'},
                        )
                    

                    
                    else:
                    
                        # Allow request if correct image
                        return (
                            jsonify(
                                {
                                    'apiVersion': 'admission.k8s.io/v1',
                                    'kind': 'AdmissionReview',
                                    'response': {
                                        'uid': admission_review.request.uid,
                                        'allowed': True,
                                    },
                                }
                            ),
                            200,
                            {'content-type': 'application/json'},
                        )
                    
        # If container or image not found deny
        return (
            jsonify(
                {
                    'apiVersion': 'admission.k8s.io/v1',
                    'kind': 'AdmissionReview',
                    'response': {
                        'uid': admission_review.request.uid,
                        'allowed': False,
                        'status': {'message': 'Required container or image not found.'},
                    },
                }
            ),
            200,
            {'content-type': 'application/json'},
        )

    return app
        