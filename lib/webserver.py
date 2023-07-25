import socketpool
import asyncio
import os
import wifi
import collections
from log import info

client_queue = collections.deque((), 5)
http_version = "HTTP/1.1"
WORKERS = 5
WORKER_BUFFER_SIZE = 1024


class Router:
    table = {
        "": "index.html",
        "water": "index.html"
    }


class Client:
    def __init__(self, socket: socketpool.Socket, address: tuple[int, str]):
        self.socket = socket
        self.address = address
        
    def send(self, *args):
        """Try to send data to client.
    
        If sending data fails, close the socket.
        """
        try:
            self.socket.send(*args)
        except:
            self.close()
    
    def close(self):
        if self.socket:
            self.socket.close()
            
    @property
    def ip(self):
        return self.address[0]
    
    @property
    def port(self):
        return self.address[1]
    
    async def recv_data(self, buffer, buffer_size) -> int:
        """Asynchronesly receive data into buffer.
        
        If not data is ready to receive, yield to the scheduler to avoid
        blocking.
        
        Parameters
        ----------
        buffer: bytearray
            Buffer to read data into.
        buffer_size: int
            The maximum amount of data that can be read.
            
        Returns
        -------
        data_read: int
            The amount of data read into the buffer.
        """
        while True:
            try:
                read_bytes = self.socket.recv_into(buffer, buffer_size)
                return read_bytes
            except OSError:
                await asyncio.sleep(0)
                continue


async def accept_connection(socket) -> Client:
    """Asynchronesly accept incomming connections.

    If accepting a connection fails (ie. No conneciton to accept), yield to the
    scheduler to avoid blocking.
    
    Parameters
    ----------
    socket: socketpool.Socket
        Server socket.
    
    Returns
    -------
    client: Client
        Client object that is representing the connection.
    """
    while True:
        try:
            csocket, address = socket.accept()
            return Client(csocket, address)
        except OSError:
            await asyncio.sleep(0)


def extract_request(request: bytearray) -> tuple[Optional[str],
                                                 Optional[str],
                                                 Optional[dict],
                                                 Optional[dict]]:
    """Extract data from HTTP request.

    Extracts headers, arguments, path and method in request.
    
    Parameters
    ----------
    request: bytearray
        Bytearray of raw request data.
        
    Returns
    -------
    path: str, optional
        The path requested.
    method: str, optional
        The HTTP method used in the request.
    headers: dict, optional
        All headers in the request.
    kwargs: dict, optional
        Any arguments supplied in the request.
    """
    try:
        headers = {}
        kwargs = {}
        _str = request.decode()
        header, _, body = _str.partition("\r\n\r\n")
        lines = header.splitlines()
        method, path, _ = lines[0].split()
        path, _, args = path.partition("?")
        
        def extract_args(args: str) -> None:
            """Extract arguments from string and put in kwargs dict.
            
            Parameters
            ----------
            args: str
                Argument string.
            """
            args = args.split("&")
            for arg in args:
                if not arg:
                    continue
                key, _, value = arg.partition("=")
                kwargs[key] = value
                
        for line in lines[1:]:
            if not line:
                continue
            key, _, value = line.partition(":")
            value = value.strip()
            headers[key] = value
        if body:
            lines = body.splitlines()
            for line in lines:
                extract_args(line)
        return path, method, headers, kwargs
    except:
        return None, None, None, None


async def handle_connection(client: Client, buffer: bytearray,
                            buffer_size: int) -> None:
    """Handle incoming connection.
    
    Handles HTTP requests and calls API endpoint if needed.
    
    Parameters
    ----------
    client: Client
        Client object that is representing the connection.
    buffer: bytearray
        Buffer to store read and write data in.
    buffer_size: int
        Size of the buffer.
    """
    global http_version
    read_bytes = await client.recv_data(buffer, buffer_size)
    extracted = extract_request(buffer[:read_bytes])
    response = await WebAPI.handle_api(*extracted)
    path, method, headers, kwargs = extracted
    
    if path is None:
        client.close()
        return
    
    if response is None:
        html, rc = path2html(path)
    elif isinstance(response, tuple):
        rc = str(response[0]) + response[1]
        if len(response) == 3:
            html = response[2]
        else:
            html, _ = path2html(path if response[0] == 200 else "/error.html")
    else:
        html, rc = response, "200 OK"
    response = f"{http_version} {rc}\r\n\r\n{html}"
    client.send(response)
    client.close()


async def worker(_id: int, buffer_size: int) -> None:
    """Worker handles clients in the client queue.

    Parameters
    ----------
    _id: int
        Id of the worker.
    buffer_size: int
        Size of the buffer to allocate.
    """
    buffer = bytearray(buffer_size)
    while True:
        try:
            client = client_queue.popleft()
            await handle_connection(client, buffer, buffer_size)
        except IndexError:
            await asyncio.sleep(0)


async def Webserver(port: int) -> None:
    """Setup workers and start server loop.

    Parameters
    ----------
    port: int
        Port to host HTTP server on.
    """
    server_workers = []
    for i in range(WORKERS):
        server_workers.append(worker(i, WORKER_BUFFER_SIZE))
    await asyncio.gather(server_loop(port), *server_workers)


async def server_loop(port: int) -> None:
    """Accept incoming connections.

    Spawns new asyncio task for each client connecting to prevent blocking when
    slow clients are sending or receiveing data.
    
    Parameters
    ----------
    port: int
        Port to host HTTP server on.
    """
    sp = socketpool.SocketPool(wifi.radio)
    s = sp.socket()
    s.bind(("", port))
    s.listen(5)
    s.setblocking(False)
    http_version = "HTTP/1.1"
    while True:
        client = await accept_connection(s)
        client_queue.append(client)


def path2html(path: str) -> str:
    """Get html from path.

    If `path` is not found as a file, recursive call with new path from routing
    table.
    WARNING: Can get stuck if two routes point to eachother.
    
    Parameters
    ----------
    path: str
        HTTP path to find html file for.
    
    Returns
    -------
    html: str
        The HTML representing the path.
    """
    path = path.strip("/")
    files = os.listdir("/html")
    if path not in files:
        npath = Router.table.get(path)
        if npath:
            return path2html(npath)
        with open("/html/error.html") as f:
            return f.read(), "404 Not Found"
    for file in files:
        if not file == path:
            continue
        with open("/html/" + file) as f:
            return f.read(), "200 OK"


def endpoint(path: str, method: str="POST"):
    """Factory to add a function as an endpoint to WebAPI class.

    Parameters
    ----------
    path: str
        Path to the API endpoint.
    method: str
        Method required to access the endpoint.
    """
    def decorator(func):
        WebAPI.endpoints[path] = Endpoint(path, method, func)
        return func
    return decorator


class Endpoint:
    def __init__(self, path: str, method: str, func: Callable):
        """Initialize an endpoint object.

        Parameters
        ----------
        path: str
            Path to the API endpoint.
        method: str
            Method required to access the endpoint.
        func: Callable
            Function/Coroutine to be called when accessing API.
        """
        self.path = path
        self.method = method
        self.func = func
    
    def __hash__(self) -> int:
        return hash(self.path)


class WebAPI:
    """A collection of all endpoints registered on the class."""
    endpoints = {}
    
    @classmethod
    async def handle_api(cls, path: str, method: str, headers: dict,
                         kwargs: dict) -> Untion[tuple[int, str, str],
                                                str, tuple[int, str]]:
        """Handle API request and return response.
        
        Parameters
        ----------
        path: str
            API endpoint.
        method: str
            HTTP method allowed.
        headers: dict
            HTTP request headers.
        kwargs: dict
            HTTP request arguments.
        """
        endpoint = cls.endpoints.get(path)
        if endpoint is None:
            return
        if endpoint.method == method:
            rt = await endpoint.func(**kwargs)
            if rt is not None:
                return rt # (Return code, return message)
                          # html
                          # (Return coce, return message, html)