"""
Event Sourcing Projection Manager

Manages all event projections and provides query interface.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from trading_agent.events.projections import (
    OrderProjection,
    PortfolioProjection,
    PositionProjection,
    Projection,
    RiskProjection,
    SignalProjection,
    TradeProjection,
)
from trading_agent.events.store import EventStore

logger = logging.getLogger(__name__)


@dataclass
class ProjectionManager:
    """Manages multiple event projections."""

    event_store: EventStore
    projections: dict[str, Projection] = field(default_factory=dict)
    _running: bool = False
    _task: Optional[asyncio.Task] = None
    _last_position: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        # Register default projections
        self.register_projection("trades", TradeProjection())
        self.register_projection("positions", PositionProjection())
        self.register_projection("portfolio", PortfolioProjection())
        self.register_projection("risk", RiskProjection())
        self.register_projection("orders", OrderProjection())
        self.register_projection("signals", SignalProjection())

    def register_projection(self, name: str, projection: Projection) -> None:
        """Register a projection."""
        self.projections[name] = projection
        self._last_position[name] = 0
        logger.info(f"Registered projection: {name}")

    async def start(self) -> None:
        """Start projection processing."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Projection manager started")

    async def stop(self) -> None:
        """Stop projection processing."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Projection manager stopped")

    async def _process_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                # Process each projection
                for name, projection in self.projections.items():
                    position = self._last_position.get(name, 0)

                    # Get new events
                    events = await self.event_store.get_events(
                        stream_name="all",
                        from_position=position,
                        max_count=100,
                    )

                    for event in events:
                        await projection.project(event)
                        position = event.position

                    self._last_position[name] = position

                await asyncio.sleep(1)  # Poll interval

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Projection processing error: {e}")
                await asyncio.sleep(5)

    async def rebuild_all(self, from_position: int = 0) -> None:
        """Rebuild all projections from event store."""
        logger.info(f"Rebuilding all projections from position {from_position}")

        for name, projection in self.projections.items():
            self._last_position[name] = from_position

        # Process all events
        position = from_position
        while True:
            events = await self.event_store.get_events(
                stream_name="all",
                from_position=position,
                max_count=1000,
            )

            if not events:
                break

            for event in events:
                for projection in self.projections.values():
                    await projection.project(event)
                position = event.position

            if len(events) < 1000:
                break

        for name in self.projections:
            self._last_position[name] = position

        logger.info(f"Rebuild complete, final position: {position}")

    async def get_projection_state(self, name: str) -> dict[str, Any]:
        """Get state of a specific projection."""
        projection = self.projections.get(name)
        if not projection:
            raise ValueError(f"Projection not found: {name}")
        return await projection.get_state()

    async def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Get state of all projections."""
        return {
            name: await projection.get_state()
            for name, projection in self.projections.items()
        }

    def get_projection(self, name: str) -> Optional[Projection]:
        """Get projection by name."""
        return self.projections.get(name)


# CLI for projection management
import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer(name="projection", help="Event sourcing projections")


@app.command()
def rebuild(
    event_store_path: str = typer.Argument(..., help="Path to event store"),
    from_position: int = typer.Option(0, "--from", help="Position to rebuild from"),
    projection: str = typer.Option(
        None, "--projection", "-p", help="Specific projection to rebuild"
    ),
):
    """Rebuild projections from event store."""
    import asyncio

    async def _rebuild():
        store = EventStore(event_store_path)
        await store.connect()

        manager = ProjectionManager(store)

        if projection:
            # Rebuild single projection
            proj = manager.get_projection(projection)
            if not proj:
                console.print(f"[red]Projection not found: {projection}[/red]")
                return

            position = from_position
            while True:
                events = await store.get_events(
                    "all", from_position=position, max_count=1000
                )
                if not events:
                    break
                for event in events:
                    await proj.project(event)
                    position = event.position
                if len(events) < 1000:
                    break

            console.print(f"[green]Rebuilt {projection} to position {position}[/green]")
        else:
            # Rebuild all
            await manager.rebuild_all(from_position)
            console.print("[green]All projections rebuilt[/green]")

        await store.disconnect()

    asyncio.run(_rebuild())


@app.command()
def status(
    event_store_path: str = typer.Argument(..., help="Path to event store"),
    projection: str = typer.Option(
        None, "--projection", "-p", help="Specific projection"
    ),
):
    """Show projection status."""
    import asyncio

    async def _status():
        store = EventStore(event_store_path)
        await store.connect()

        manager = ProjectionManager(store)

        if projection:
            state = await manager.get_projection_state(projection)
            console.print(f"\n[bold]{projection} state:[/bold]")
            _print_state(state)
        else:
            states = await manager.get_all_states()
            for name, state in states.items():
                console.print(f"\n[bold]{name}:[/bold]")
                _print_state(state)

        await store.disconnect()

    def _print_state(state: dict):
        if not state:
            console.print("  (empty)")
            return

        table = Table("Key", "Value")
        for k, v in state.items():
            if isinstance(v, dict):
                table.add_row(k, f"{len(v)} items")
            elif isinstance(v, list):
                table.add_row(k, f"{len(v)} items")
            else:
                table.add_row(k, str(v))
        console.print(table)

    asyncio.run(_status())


@app.command()
def query(
    event_store_path: str = typer.Argument(..., help="Path to event store"),
    projection: str = typer.Argument(..., help="Projection name"),
    key: str = typer.Argument(None, help="Specific key to query"),
):
    """Query projection state."""
    import asyncio

    async def _query():
        store = EventStore(event_store_path)
        await store.connect()

        manager = ProjectionManager(store)
        state = await manager.get_projection_state(projection)

        if key:
            # Navigate to key
            parts = key.split(".")
            value = state
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break
            console.print(f"{key}: {value}")
        else:
            _print_state(state)

        await store.disconnect()

    def _print_state(state: dict, indent: int = 0):
        prefix = "  " * indent
        for k, v in state.items():
            if isinstance(v, dict):
                console.print(f"{prefix}{k}:")
                _print_state(v, indent + 1)
            elif isinstance(v, list):
                console.print(f"{prefix}{k}: [{len(v)} items]")
                if v and isinstance(v[0], dict):
                    console.print(f"{prefix}  First: {v[0]}")
            else:
                console.print(f"{prefix}{k}: {v}")

    asyncio.run(_query())


if __name__ == "__main__":
    app()
