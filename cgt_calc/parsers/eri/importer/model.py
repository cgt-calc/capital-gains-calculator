"""Model classes for ERI Importer."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from cgt_calc.parsers.eri.model import ERITransaction


@dataclass
class ERIImporterOutput:
    """Output of an ERI Importer."""

    transactions: list[ERITransaction]
    output_file_name: str


class ERIImporter(ABC):
    """Base class for all ERI importers."""

    def __init__(self, name: str):
        """Create a new instance with the given name."""
        self.name = name

    @abstractmethod
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse the input file.

        Return None when the file is not accepted by the parser.
        """
