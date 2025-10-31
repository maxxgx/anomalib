# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import asyncio
from collections.abc import Callable
from enum import StrEnum
from multiprocessing.synchronize import Condition
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_async_db_session_ctx
from pydantic_models import Sink, Source
from repositories import PipelineRepository, SinkRepository, SourceRepository
from services import ActivePipelineService
from services.exceptions import ResourceNotFoundError, ResourceType


class PipelineField(StrEnum):
    """Enumeration for pipeline fields that can trigger configuration reloads."""

    SOURCE_ID = "source_id"
    SINK_ID = "sink_id"


class ConfigurationService:
    def __init__(self, active_pipeline_service: ActivePipelineService, config_changed_condition: Condition) -> None:
        self._active_pipeline_service: ActivePipelineService = active_pipeline_service
        self._config_changed_condition: Condition = config_changed_condition

    def _notify_sink_changed(self) -> None:
        """Notify that sink configuration has changed by reloading the active pipeline service."""
        try:
            # Try to get the current event loop
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._active_pipeline_service.reload())
            task.add_done_callback(lambda _: logger.debug("Sink changed notified"))
        except RuntimeError:
            # If no event loop is running, create a new one
            asyncio.run(self._active_pipeline_service.reload())

    def _notify_source_changed(self) -> None:
        with self._config_changed_condition:
            self._config_changed_condition.notify_all()

    @staticmethod
    async def _on_config_changed(
        config_id: UUID, field: PipelineField, db: AsyncSession, notify_fn: Callable[[], None]
    ) -> None:
        """Notify threads or child processes that the configuration has changed.
        Notification triggered only when the configuration is used by the active pipeline."""
        pipeline_repo = PipelineRepository(db)
        active_pipeline = await pipeline_repo.get_active_pipeline()
        if active_pipeline and str(getattr(active_pipeline, field)) == str(config_id):
            notify_fn()

    async def list_sources(self, project_id: UUID) -> list[Source]:
        async with get_async_db_session_ctx() as db:
            source_repo = SourceRepository(db, project_id=project_id)
            return await source_repo.get_all()

    async def list_sinks(self, project_id: UUID) -> list[Sink]:
        async with get_async_db_session_ctx() as db:
            sink_repo = SinkRepository(db, project_id=project_id)
            return await sink_repo.get_all()

    async def get_source_by_id(self, source_id: UUID, project_id: UUID, db: AsyncSession | None = None) -> Source:
        if db is None:
            async with get_async_db_session_ctx() as db_session:
                source_repo = SourceRepository(db_session, project_id=project_id)
                source = await source_repo.get_by_id(source_id)
        else:
            source_repo = SourceRepository(db, project_id=project_id)
            source = await source_repo.get_by_id(source_id)
        if not source:
            raise ResourceNotFoundError(ResourceType.SOURCE, str(source_id))
        return source

    async def get_sink_by_id(self, sink_id: UUID, project_id: UUID, db: AsyncSession | None = None) -> Sink:
        if db is None:
            async with get_async_db_session_ctx() as db_session:
                sink_repo = SinkRepository(db_session, project_id=project_id)
                sink = await sink_repo.get_by_id(sink_id)
        else:
            sink_repo = SinkRepository(db, project_id=project_id)
            sink = await sink_repo.get_by_id(sink_id)
        if not sink:
            raise ResourceNotFoundError(ResourceType.SINK, str(sink_id))
        return sink

    async def create_source(self, source: Source) -> Source:
        async with get_async_db_session_ctx() as db:
            source_repo = SourceRepository(db, project_id=source.project_id)
            return await source_repo.save(source)

    async def create_sink(self, sink: Sink) -> Sink:
        async with get_async_db_session_ctx() as db:
            sink_repo = SinkRepository(db, project_id=sink.project_id)
            return await sink_repo.save(sink)

    async def update_source(self, source_id: UUID, project_id: UUID, partial_config: dict) -> Source:
        async with get_async_db_session_ctx() as db:
            source = await self.get_source_by_id(source_id, project_id, db)
            source_repo = SourceRepository(db, project_id=project_id)
            updated = await source_repo.update(source, partial_config)
            await self._on_config_changed(updated.id, PipelineField.SOURCE_ID, db, self._notify_source_changed)
            return updated

    async def update_sink(self, sink_id: UUID, project_id: UUID, partial_config: dict) -> Sink:
        async with get_async_db_session_ctx() as db:
            sink = await self.get_sink_by_id(sink_id, project_id, db)
            sink_repo = SinkRepository(db, project_id=project_id)
            updated = await sink_repo.update(sink, partial_config)
            await self._on_config_changed(updated.id, PipelineField.SINK_ID, db, self._notify_sink_changed)
            return updated

    async def delete_source_by_id(self, source_id: UUID, project_id: UUID) -> None:
        async with get_async_db_session_ctx() as db:
            source = await self.get_source_by_id(source_id, project_id, db)
            source_repo = SourceRepository(db, project_id=project_id)
            await source_repo.delete_by_id(source.id)

    async def delete_sink_by_id(self, sink_id: UUID, project_id: UUID) -> None:
        async with get_async_db_session_ctx() as db:
            sink = await self.get_sink_by_id(sink_id, project_id, db)
            sink_repo = SinkRepository(db, project_id=project_id)
            await sink_repo.delete_by_id(sink.id)
