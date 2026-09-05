from .base import BaseConnector
from .cppp import CPPPConnector
from .data_gov_in import DataGovInConnector
from .gepnic import (
    GePNICConnector,
    HPTendersConnector,
    MPTendersConnector,
    RajasthanTendersConnector,
)

# Every connector the scheduler and CLI know about.
# There is deliberately no GeM entry, and adding one raises at import time
# (see app/compliance.py, rule #1).
REGISTRY: dict[str, type[BaseConnector]] = {
    CPPPConnector.source_name: CPPPConnector,
    DataGovInConnector.source_name: DataGovInConnector,
    MPTendersConnector.source_name: MPTendersConnector,
    HPTendersConnector.source_name: HPTendersConnector,
    RajasthanTendersConnector.source_name: RajasthanTendersConnector,
}

__all__ = [
    "BaseConnector",
    "CPPPConnector",
    "DataGovInConnector",
    "GePNICConnector",
    "HPTendersConnector",
    "MPTendersConnector",
    "RajasthanTendersConnector",
    "REGISTRY",
]
