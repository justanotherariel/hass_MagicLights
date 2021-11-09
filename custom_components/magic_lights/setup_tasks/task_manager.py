from __future__ import annotations

from os import posix_fadvise
from typing import Dict, List
from custom_components.magic_lights.magicbase.share import get_magic
from custom_components.magic_lights.setup_tasks.task import SetupTask
import asyncio
from importlib import import_module
from pathlib import Path
import logging
import collections


class TaskManager:
    """MagicLights setup task manager."""

    def __init__(self) -> None:
        """Initialize the setup manager class."""
        self.magic = get_magic()
        self.log = logging.getLogger(__name__)

        self.__tasks: Dict[str, SetupTask] = {}

    @property
    def tasks(self) -> List[SetupTask]:
        """Return all list of all tasks."""
        return list(self.__tasks.values())

    async def async_load(self) -> None:
        """Load all tasks."""
        task_files = Path(__file__).parent
        task_modules = (
            module.stem
            for module in task_files.glob("*.py")
            if module.name not in ("__init__.py", "task.py", "task_manager.py")
        )

        for module_name in task_modules:
            task_module = import_module(f"{__package__}.{module_name}")
            if task := task_module.Task():
                self.__tasks[task.slug] = task

        stages = {}
        for task in self.tasks:
            if not hasattr(task, "stage"):
                task.stage = 100
            if task.stage not in stages:
                stages.update({task.stage: []})

            stages[task.stage].append(task)

        stages = collections.OrderedDict(sorted(stages.items()))

        # Setup all other tasks
        self.log.info("Loaded %s stages with %s tasks", len(stages), len(self.tasks))

        for stage, tasks in stages.items():
            self.log.debug("Executing Stage %s", stage)
            await asyncio.gather(*[task.execute() for task in tasks])
