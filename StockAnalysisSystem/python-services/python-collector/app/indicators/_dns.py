"""Bound OS DNS work without tying verification deadlines to executor shutdown.

An OS resolver cannot be forcibly canceled safely. At most two daemon lookups
may remain after cancellation; further requests fail fast instead of spawning
unlimited threads or retaining a source write fence until getaddrinfo returns.
"""
import asyncio
import socket
from threading import BoundedSemaphore, Thread

from aiohttp.abc import AbstractResolver

_SLOTS = BoundedSemaphore(2)


class BoundedResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        if not _SLOTS.acquire(blocking=False):
            raise OSError('Indicator DNS resolver capacity unavailable')
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def finish(result, error):
            if not future.done():
                if error is not None:
                    future.set_exception(error)
                else:
                    future.set_result(result)

        def lookup():
            result = []; error = None
            try:
                infos = socket.getaddrinfo(host,port,type=socket.SOCK_STREAM,
                    family=family,flags=socket.AI_ADDRCONFIG)
                for resolved_family, _, proto, _, address in infos:
                    if resolved_family not in (socket.AF_INET,socket.AF_INET6):
                        continue
                    if resolved_family==socket.AF_INET6 and len(address)>3 and address[3]:
                        numeric, service = socket.getnameinfo(address,socket.NI_NUMERICHOST|socket.NI_NUMERICSERV)
                        resolved_port = int(service)
                    else:
                        numeric, resolved_port = address[:2]
                    result.append(dict(hostname=host,host=numeric,port=resolved_port,
                        family=resolved_family,proto=proto,flags=socket.AI_NUMERICHOST|socket.AI_NUMERICSERV))
            except Exception as caught:
                error = caught
            finally:
                _SLOTS.release()
            try:
                loop.call_soon_threadsafe(finish,result,error)
            except RuntimeError:
                pass  # The timed-out owner has already closed its event loop.

        try:
            Thread(target=lookup,name='indicator-dns',daemon=True).start()
        except Exception:
            _SLOTS.release()
            raise OSError('Indicator DNS resolver unavailable') from None
        return await future

    async def close(self):
        # Connector cancellation ends awaiting tasks; daemon OS work is bounded
        # globally and never joined while a verification fence is held.
        pass
