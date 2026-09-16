from .base import BaseConnector
from .cppp import CPPPConnector
from .data_gov_in import DataGovInConnector
from .gem import GeMConnector
from .gepnic import STATE_INSTANCES, GePNICConnector

# Built from gepnic.STATE_INSTANCES so a new verified state is one table row, not
# an edit in three files.
_GEPNIC = {
    source: getattr(__import__("app.connectors.gepnic", fromlist=["x"]), cls)
    for cls, (source, _host) in STATE_INSTANCES.items()
}

REGISTRY: dict[str, type[BaseConnector]] = {
    CPPPConnector.source_name: CPPPConnector,
    DataGovInConnector.source_name: DataGovInConnector,
    GeMConnector.source_name: GeMConnector,
    **_GEPNIC,
}

# Re-exported so `from app.connectors import MPTendersConnector` keeps working.
globals().update({cls: _GEPNIC[source] for cls, (source, _) in STATE_INSTANCES.items()})

__all__ = [
    "BaseConnector",
    "CPPPConnector",
    "DataGovInConnector",
    "GeMConnector",
    "GePNICConnector",
    "STATE_INSTANCES",
    "REGISTRY",
    *STATE_INSTANCES,
]
