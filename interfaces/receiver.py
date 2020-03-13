from interfaces.client import Client
from threading import Lock
from queue import Queue
import aiofiles
import asyncio
import os
import gc

TEMP_LOCATION = ".asdkjasdkasdhlsadhsajdhlas"
ASYNC_POOL_SIZE = 50
BUFFER_SIZE = 65536
USED_PORTS = 80


async def write_file_thread(location, data):
    async with aiofiles.open(location, mode="wb") as file:
        await file.write(data)


async def receive_data_thread(port, ip, location, loop):
    try:
        reader, writer = await asyncio.open_connection(ip, port, loop=loop)
    except ConnectionRefusedError:
        await asyncio.sleep(1)
        try:
            reader, writer = await asyncio.open_connection(ip, port, loop=loop)
        except ConnectionRefusedError:
            return 0
    except ConnectionResetError:
        return 0

    file_name = await reader.read(10)
    file_name = file_name.decode("utf-8").strip()
    received_bytes = []

    while True:
        temp = await reader.read()
        if temp:
            received_bytes.append(temp)
        else:
            break
    writer.close()

    data = b"".join(received_bytes)

    if data:
        location = os.path.join(os.path.sep.join(location.split(os.path.sep)[:-1]),
                                TEMP_LOCATION, file_name)
        await write_file_thread(location, data)

    return len(data)


async def receive_data_process_async(port, ip, location, event_loop):
    completed_bytes = ReceivedData()

    def update_hook(future):
        res = future.result()
        if res:
            completed_bytes.data += res

    task = asyncio.create_task(receive_data_thread(port, ip, location, event_loop))
    task.add_done_callback(update_hook)

    await task

    return completed_bytes.data, port


async def receive_data_process(port, ip, location, event_loop):
    return await receive_data_process_async(port, ip, location, event_loop)


class ReceivedData:
    def __init__(self, pipe=None):
        self.pipe = pipe
        self.data = 0


class Receiver(Client):
    def __init__(self, ip, save_file_path):
        super().__init__(ip, save_file_path)
        self.save_location = ""
        self.ip = ip

    def get_file_name(self):
        _, file_name = os.path.split(self.save_file_location)
        return file_name

    async def fetch_data_async(self, ip, connection_pipe, child_pipe, process_loop):
        reader, writer = await asyncio.open_connection(ip, self.get_port(), loop=process_loop)
        header = await reader.read(200)

        writer.close()

        del writer
        del reader

        val = header.split()
        size = int(val[0])
        file_name = " ".join([val[i].decode("utf-8") for i in range(1, len(val))])

        save_location = os.path.join(self.save_file_location, file_name)
        ports = Queue()

        for port in list(range(30000, 30000 + USED_PORTS)):
            ports.put(port)

        os.makedirs(os.path.join(os.path.sep.join(save_location.split(os.path.sep)[:-1]),
                                 TEMP_LOCATION), exist_ok=True)

        r = ReceivedData(connection_pipe)

        lock = Lock()

        def update_hook(value):
            res = value.result()
            if res:
                with lock:
                    r.data += res[0]
                    ports.put(res[1])
                    r.pipe.send((r.data / size) * 100)

        tasks = []

        for i, _ in enumerate(range(0, size, BUFFER_SIZE)):
            if ports.qsize() != 0:
                port = ports.get()
                temp_tasks = []
                while len(tasks) != 0:
                    temp_tasks.append(tasks.pop())
                    if len(temp_tasks) == 10:
                        break
                await asyncio.gather(*temp_tasks)
            else:
                await asyncio.gather(*tasks)
                tasks = []
                port = ports.get()

            task = asyncio.create_task(receive_data_process(port, ip,
                                                            save_location, process_loop))
            task.add_done_callback(update_hook)
            tasks.append(task)

            if r.data == size:
                break

        await asyncio.gather(*tasks)

        del ports

        gc.collect()

        self.save_location = save_location
        child_pipe.send(save_location)

    async def write_data(self, save_location, ui_element):
        async with aiofiles.open(save_location, mode="wb") as file:
            path = os.path.join(os.path.sep.join(save_location.split(os.path.sep)[:-1]),
                                TEMP_LOCATION)

            files = [i for i in os.listdir(path) if i.isnumeric()]
            for index, temp_file in enumerate(sorted(files, key=int)):
                async with aiofiles.open(os.path.join(path, temp_file), mode="rb") as temp:
                    contents = await temp.read()
                    ui_element.ui.progressBar.setValue(((index + 1) / len(files)) * 100)
                    await file.write(contents)

        return path

    def fetch_data(self, pipe, child):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.fetch_data_async(self.ip, pipe, child, loop))

        del loop
