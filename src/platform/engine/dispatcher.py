"""Central event dispatcher for the asynchronous trading engine."""

import asyncio
import logging
from typing import Dict, List, Type, Callable, Awaitable, Any

from src.platform.engine.events import Event

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[[Event], Awaitable[None]]


class EventDispatcher:
    """
    A simple async pub/sub event bus.
    Components publish events to the queue, and the dispatcher routes them
    to all registered asynchronous handlers.
    """

    def __init__(self) -> None:
        self._handlers: Dict[Type[Event], List[EventHandler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._is_running: bool = False
        self._dispatch_task: asyncio.Task[Any] | None = None

    def subscribe(self, event_type: Type[Event], handler: EventHandler) -> None:
        """Register an async handler for a specific event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
            logger.debug(f"Subscribed {handler.__name__} to {event_type.__name__}")

    def publish(self, event: Event) -> None:
        """Put an event into the processing queue without blocking."""
        self._queue.put_nowait(event)

    async def start(self) -> None:
        """Start the dispatcher loop in the background."""
        if self._is_running:
            return
        self._is_running = True
        self._dispatch_task = asyncio.create_task(self._process_queue())
        logger.info("EventDispatcher started.")

    async def stop(self) -> None:
        """Stop the dispatcher loop and wait for it to finish."""
        self._is_running = False
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None
        logger.info("EventDispatcher stopped.")

    async def _process_queue(self) -> None:
        """Inner loop to consume events and call handlers concurrently."""
        while self._is_running:
            try:
                event = await self._queue.get()
                
                # Fetch exact match handlers and base class handlers
                handlers_to_call = []
                for event_type, handlers in self._handlers.items():
                    if isinstance(event, event_type):
                        handlers_to_call.extend(handlers)
                
                if handlers_to_call:
                    # Fire all matching handlers concurrently
                    tasks = [handler(event) for handler in handlers_to_call]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # Log exceptions from handlers
                    for result in results:
                        if isinstance(result, Exception):
                            logger.error(f"Error in event handler for {type(event).__name__}: {result}", exc_info=result)
                
                self._queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in dispatcher loop: {e}", exc_info=True)
