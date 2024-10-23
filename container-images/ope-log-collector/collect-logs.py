import os
import logging
from kubernetes_asyncio import client, config, watch
import aiofiles
import asyncio
import traceback
from pydantic import BaseModel


LOG = logging.getLogger(__name__)
logging.basicConfig(level='INFO', format='%(message)s')


class LogInfo(BaseModel, arbitrary_types_allowed=True):
    v1: client.CoreV1Api
    namespace: str
    pod_name: str
    container_name: str
    pvc_name: str


async def get_client() -> client.CoreV1Api:
    try:
        await config.load_config()
        return client.CoreV1Api()
    except config.ConfigException as e:
        LOG.error('Could not configure Kubernetes client: %s', str(e))
        exit(1)


async def gather_logs(namespace: str):
    v1 = await get_client()

    LOG_DIR = './log'

    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception as e:
            LOG.error(f"Could not create log directory '{LOG_DIR}': {e}")
            exit(1)

    w = watch.Watch()
    tasks = {}
    try:
        async for event in w.stream(
            v1.list_namespaced_pod, namespace=namespace, timeout_seconds=0
        ):
            pod = event['object']
            pod_name = pod.metadata.name
            event_type = event['type']

            # Filter pods created by rhoai
            if not pod_name.startswith('jupyter-nb'):
                continue

            pvc_name = pod.spec.volumes[0].name

            # Wait until pods start up
            if event_type in ['ADDED', 'MODIFIED']:
                pod_status = pod.status.phase
                if pod_status != 'Running':
                    continue

                container_name = pod.spec.containers[0].name

                log_info = LogInfo(
                    v1=v1,
                    namespace=namespace,
                    pod_name=pod_name,
                    container_name=container_name,
                    pvc_name=pvc_name,
                )

                # Start streaming logs
                if pod_name not in tasks:
                    tasks[pod_name] = [
                        asyncio.create_task(stream_pod_logs(log_info)),
                        asyncio.create_task(stream_pod_events(log_info)),
                        asyncio.create_task(stream_pvc_events(log_info)),
                    ]

            elif event_type == 'DELETED':
                if pod_name in tasks:
                    LOG.info(f'Pod deleted: {pod_name}. Cancelling log streaming.')
                    for task in tasks[pod_name]:
                        task.cancel()
                    del tasks[pod_name]

    except asyncio.CancelledError:
        LOG.info('User cancelled run. Cancelling log streaming.')
        exit(1)

    except Exception as e:
        LOG.info(f'Unexpected Error: {e}.')


async def stream_pod_logs(log_info: LogInfo):
    log_file_path = os.path.join('./log', f'{log_info.pod_name}.log')
    LOG.info(f"Streaming logs for pod '{log_info.pod_name}' to '{log_file_path}'.")

    response = None
    try:
        response = await log_info.v1.read_namespaced_pod_log(
            name=log_info.pod_name,
            namespace=log_info.namespace,
            container=log_info.container_name,
            follow=True,
            _preload_content=False,
            timestamps=True,
        )

        # Read logs and write to log file
        async with aiofiles.open(log_file_path, 'a') as log_file:
            async for line in response.content:
                if line:
                    decoded_line = line.decode('utf-8')
                    await log_file.write(decoded_line)
                    await log_file.flush()
    except asyncio.CancelledError:
        if response:
            await response.release()
    except Exception as e:
        LOG.error(
            f"Unexpected error while streaming logs for pod '{log_info.pod_name}': {e}"
        )
        LOG.error(traceback.format_exc())
        raise
    finally:
        if response:
            await response.release()


async def stream_pod_events(log_info: LogInfo):
    log_file_path = os.path.join('./log', f'{log_info.pod_name}-pod-events.log')
    LOG.info(
        f"Streaming pod events for pod '{log_info.pod_name}' to '{log_file_path}'."
    )

    field_selector = f'involvedObject.kind=Pod,involvedObject.name={log_info.pod_name},involvedObject.namespace={log_info.namespace}'

    response = None
    try:
        events = await log_info.v1.list_namespaced_event(
            namespace=log_info.namespace, field_selector=field_selector
        )

        # Read logs and write to log file
        async with aiofiles.open(log_file_path, 'a') as log_file:
            for event in events.items:
                if event:
                    event_time = event.last_timestamp
                    event_type = event.type
                    event_message = event.message

                    event_entry = f'{event_time} | Type: {event_type} | Message: {event_message}\n'

                    await log_file.write(event_entry)
                    await log_file.flush()

    except asyncio.CancelledError:
        if response:
            await response.release()
    except Exception as e:
        LOG.error(
            f"Unexpected error while streaming events for pod '{log_info.pod_name}': {e}"
        )
        LOG.error(traceback.format_exc())
        raise
    finally:
        if response:
            await response.release()


async def stream_pvc_events(log_info: LogInfo):
    log_file_path = os.path.join('./log', f'{log_info.pod_name}-pvc-events.log')
    LOG.info(
        f"Streaming pvc events for pod '{log_info.pod_name}' to '{log_file_path}'."
    )

    field_selector = f'involvedObject.kind=PersistentVolumeClaim,involvedObject.name={log_info.pvc_name},involvedObject.namespace={log_info.namespace}'

    response = None
    try:
        events = await log_info.v1.list_namespaced_event(
            namespace=log_info.namespace, field_selector=field_selector
        )

        # Read logs and write to log file
        async with aiofiles.open(log_file_path, 'a') as log_file:
            for event in events.items:
                if event:
                    event_time = event.last_timestamp
                    event_type = event.type
                    event_message = event.message

                    event_entry = f'{event_time} | Type: {event_type} | Message: {event_message}\n'

                    await log_file.write(event_entry)
                    await log_file.flush()

    except asyncio.CancelledError:
        if response:
            await response.release()
    except Exception as e:
        LOG.error(
            f"Unexpected error while streaming pvc events for pod '{log_info.pod_name}': {e}"
        )
        LOG.error(traceback.format_exc())
        raise
    finally:
        if response:
            await response.release()


if __name__ == '__main__':
    namespace_name = os.environ.get('NAMESPACE', 'rhods-notebooks')

    if not namespace_name:
        LOG.error('NAMESPACE environment variable is required.')
        exit(1)

    asyncio.run(gather_logs(namespace_name))
