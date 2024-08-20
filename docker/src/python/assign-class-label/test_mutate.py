import pytest
import mutate

from unittest import mock


@pytest.fixture()
def app():
    with mock.patch('mutate.get_group_members') as mock_group_members:
        mock_group_members.return_value = ['testuser1', 'testuser2']
        app = mutate.create_app(GROUPS='group1,group2', LABEL='testlabel')
        app.config.update(
            {
                'TESTING': True,
            }
        )

        # other setup can go here

        yield app

    # clean up / reset resources here


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.mark.xfail(reason='keyerror in code')
def test_mutate_bad_data(client):
    res = client.post('/mutate', json={})
    breakpoint()
    pass


def test_bad_path(client):
    res = client.get('/lsdkfjklsjdf')
    assert res.status_code == 404


def test_request_no_user(client):
    res = client.post(
        '/mutate',
        json={
            'request': {
                'uid': '1234',
                'object': {
                    'metadata': {}
                },
            }
        },
    )

    assert res.status_code == 200
    assert res.json == {
        'apiVersion': 'admission.k8s.io/v1',
        'kind': 'AdmissionReview',
        'response': {
            'allowed': True,
            'status': {'message': 'No class label assigned.'},
            'uid': '1234',
        },
    }

    pass


def test_api_exception(client):
    mutate.get_group_members.side_effect = ValueError("this is a test")
    res = client.post(
        '/mutate',
        json={
            'request': {
                'uid': '1234',
                'object': {
                    'metadata': {
                        'labels': {
                            'opendatahub.io/user': 'testuser1',
                        }
                    }
                },
            }
        },
    )
    assert res.status_code == 500
    assert res.text == 'unexpected error encountered'
