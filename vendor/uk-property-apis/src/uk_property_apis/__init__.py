"""UK property government and public API clients."""

from __future__ import annotations

from uk_property_apis.airquality import (
    AirQualityReading,
    AirQualityStation,
    DefraAirQualityClient,
    Pollutant,
    StationProximity,
)
from uk_property_apis.bgs import (
    BGSClient,
    ClimateProjection,
    GeohazardAssessment,
    LandslideEvent,
    ShrinkSwellAssessment,
    ShrinkSwellClass,
    ShrinkSwellHorizon,
)
from uk_property_apis.coastal import (
    ClimateUplift,
    CoastalErosionClient,
    ErosionZone,
    HorizonYear,
    ManagementScenario,
    SMPPolicy,
    ShorelinePrediction,
)
from uk_property_apis.elevation import ElevationClient, ElevationPoint
from uk_property_apis.companies_house import (
    CompaniesHouseClient,
    LandlordGraph,
    LandlordGraphEdge,
    LandlordGraphNode,
    build_landlord_graph,
)
from uk_property_apis.epc import EPCClient
from uk_property_apis.flood import FloodClient
from uk_property_apis.natural_england import (
    AONBArea,
    AncientWoodlandArea,
    Designations,
    GreenBeltArea,
    NationalParkArea,
    NaturalEnglandClient,
    SSSIArea,
)
from uk_property_apis.ons_nomis import (
    ClaimantCount,
    EmploymentStats,
    JobDensity,
    NomisClient,
    NomisDataset,
    NomisObservations,
    PopulationBreakdown,
    WageStats,
)
from uk_property_apis.idox import (
    ApplicationDetail,
    ArcGISPlanningClient,
    CouncilConfig,
    HTMLPlanningClient,
    PlanningApplication,
)
from uk_property_apis.land_registry import LandRegistryClient
from uk_property_apis.ons import ONSClient
from uk_property_apis.planning import PlanningClient
from uk_property_apis.police import PoliceClient
from uk_property_apis.postcodes import PostcodesClient
from uk_property_apis.radon import BGSRadonClient, RadonBand, RadonPotential
from uk_property_apis.tenders import (
    ContractsFinderClient,
    FTSClient,
    Tender,
    TenderClassification,
    TenderLocation,
    TenderOrg,
    TenderQuery,
    TenderSource,
    TenderStatus,
    TenderValue,
)
from uk_property_apis.voa import VOAClient

__version__ = "0.1.0"

__all__ = [
    "AONBArea",
    "AirQualityReading",
    "AirQualityStation",
    "AncientWoodlandArea",
    "ApplicationDetail",
    "ArcGISPlanningClient",
    "BGSClient",
    "BGSRadonClient",
    "ClaimantCount",
    "ClimateProjection",
    "ClimateUplift",
    "CoastalErosionClient",
    "CompaniesHouseClient",
    "ContractsFinderClient",
    "CouncilConfig",
    "DefraAirQualityClient",
    "Designations",
    "EPCClient",
    "ElevationClient",
    "ElevationPoint",
    "EmploymentStats",
    "ErosionZone",
    "GeohazardAssessment",
    "GreenBeltArea",
    "HorizonYear",
    "FTSClient",
    "FloodClient",
    "HTMLPlanningClient",
    "JobDensity",
    "LandRegistryClient",
    "LandlordGraph",
    "LandlordGraphEdge",
    "LandlordGraphNode",
    "LandslideEvent",
    "ManagementScenario",
    "NationalParkArea",
    "NomisClient",
    "NomisDataset",
    "NomisObservations",
    "NaturalEnglandClient",
    "ONSClient",
    "PlanningApplication",
    "PlanningClient",
    "PoliceClient",
    "Pollutant",
    "PopulationBreakdown",
    "PostcodesClient",
    "RadonBand",
    "RadonPotential",
    "SMPPolicy",
    "SSSIArea",
    "ShorelinePrediction",
    "ShrinkSwellAssessment",
    "ShrinkSwellClass",
    "ShrinkSwellHorizon",
    "StationProximity",
    "Tender",
    "TenderClassification",
    "TenderLocation",
    "TenderOrg",
    "TenderQuery",
    "TenderSource",
    "TenderStatus",
    "TenderValue",
    "VOAClient",
    "WageStats",
    "__version__",
    "build_landlord_graph",
]
