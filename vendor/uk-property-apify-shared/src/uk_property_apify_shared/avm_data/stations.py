"""Curated national UK rail-station dataset for the hosted AVM actor.

The public :mod:`uk_property_avm.features` module ships a minimal seed of
six London termini — enough to exercise the haversine-distance code
path and the "nearest station / stations-within-1km" output fields on
an open-source demo, but not nearly enough to give a useful
accessibility feature to a real AVM.

This private dataset covers every major terminus, interchange, and
regional hub across England, Scotland, Wales, and Northern Ireland
(~60 stations today, expanding as we enrol more secondary lines). It's
the thing the hosted A10 ``uk-avm`` actor uses to produce a
statistically useful nearest-station feature for any UK postcode.

Consumers: the A10 actor dispatch passes ``PRIVATE_STATIONS`` to the
:class:`NeighbourhoodFeatureExtractor` instead of letting it fall back
to the public ``_DEFAULT_STATIONS`` seed, giving hosted runs accurate
station-proximity features country-wide while the open-source library
keeps a minimal demo surface.
"""

from __future__ import annotations

from typing import Final

from uk_property_avm.features import Station

PRIVATE_STATIONS: Final[tuple[Station, ...]] = (
    # London termini + major interchange stations.
    Station(crs="KGX", name="London King's Cross", lat=51.5308, lng=-0.1238),
    Station(crs="EUS", name="London Euston", lat=51.5282, lng=-0.1337),
    Station(crs="PAD", name="London Paddington", lat=51.5154, lng=-0.1755),
    Station(crs="STP", name="London St Pancras International", lat=51.5320, lng=-0.1253),
    Station(crs="LST", name="London Liverpool Street", lat=51.5179, lng=-0.0817),
    Station(crs="WAT", name="London Waterloo", lat=51.5031, lng=-0.1126),
    Station(crs="VIC", name="London Victoria", lat=51.4952, lng=-0.1441),
    Station(crs="CHX", name="London Charing Cross", lat=51.5080, lng=-0.1247),
    Station(crs="MYB", name="London Marylebone", lat=51.5223, lng=-0.1638),
    Station(crs="BFR", name="London Blackfriars", lat=51.5116, lng=-0.1037),
    Station(crs="CST", name="London Cannon Street", lat=51.5113, lng=-0.0907),
    Station(crs="FST", name="London Fenchurch Street", lat=51.5116, lng=-0.0789),
    Station(crs="MOG", name="London Moorgate", lat=51.5186, lng=-0.0886),
    Station(crs="LBG", name="London Bridge", lat=51.5049, lng=-0.0865),
    # London outer hubs.
    Station(crs="STF", name="Stratford (London)", lat=51.5416, lng=-0.0042),
    Station(crs="CLJ", name="Clapham Junction", lat=51.4644, lng=-0.1708),
    Station(crs="ZFD", name="Farringdon", lat=51.5203, lng=-0.1050),
    # Major regional termini.
    Station(crs="BHM", name="Birmingham New Street", lat=52.4776, lng=-1.8998),
    Station(crs="MAN", name="Manchester Piccadilly", lat=53.4773, lng=-2.2309),
    Station(crs="MCV", name="Manchester Victoria", lat=53.4877, lng=-2.2427),
    Station(crs="LIV", name="Liverpool Lime Street", lat=53.4076, lng=-2.9775),
    Station(crs="LDS", name="Leeds", lat=53.7946, lng=-1.5479),
    Station(crs="SHF", name="Sheffield", lat=53.3780, lng=-1.4624),
    Station(crs="NCL", name="Newcastle", lat=54.9684, lng=-1.6174),
    Station(crs="YRK", name="York", lat=53.9583, lng=-1.0931),
    Station(crs="EDB", name="Edinburgh Waverley", lat=55.9524, lng=-3.1886),
    Station(crs="GLC", name="Glasgow Central", lat=55.8590, lng=-4.2577),
    Station(crs="GLQ", name="Glasgow Queen Street", lat=55.8619, lng=-4.2509),
    Station(crs="CDF", name="Cardiff Central", lat=51.4764, lng=-3.1790),
    Station(crs="SWA", name="Swansea", lat=51.6251, lng=-3.9435),
    Station(crs="BRI", name="Bristol Temple Meads", lat=51.4491, lng=-2.5810),
    Station(crs="NWP", name="Newport (South Wales)", lat=51.5878, lng=-2.9976),
    Station(crs="BTH", name="Bath Spa", lat=51.3779, lng=-2.3568),
    Station(crs="EXD", name="Exeter St Davids", lat=50.7298, lng=-3.5434),
    Station(crs="PLY", name="Plymouth", lat=50.3784, lng=-4.1429),
    Station(crs="OXF", name="Oxford", lat=51.7535, lng=-1.2703),
    Station(crs="CBG", name="Cambridge", lat=52.1942, lng=0.1371),
    Station(crs="PBO", name="Peterborough", lat=52.5747, lng=-0.2497),
    Station(crs="NRW", name="Norwich", lat=52.6272, lng=1.3062),
    Station(crs="IPS", name="Ipswich", lat=52.0509, lng=1.1443),
    Station(crs="BMO", name="Bournemouth", lat=50.7278, lng=-1.8463),
    Station(crs="SOU", name="Southampton Central", lat=50.9073, lng=-1.4153),
    Station(crs="PMS", name="Portsmouth & Southsea", lat=50.7981, lng=-1.0920),
    Station(crs="BTN", name="Brighton", lat=50.8294, lng=-0.1410),
    Station(crs="AFK", name="Ashford International", lat=51.1455, lng=0.8760),
    Station(crs="CTB", name="Canterbury East", lat=51.2721, lng=1.0812),
    Station(crs="DVR", name="Dover Priory", lat=51.1269, lng=1.3060),
    Station(crs="RDG", name="Reading", lat=51.4589, lng=-0.9724),
    Station(crs="LUT", name="Luton", lat=51.8810, lng=-0.4145),
    Station(crs="MKC", name="Milton Keynes Central", lat=52.0346, lng=-0.7745),
    Station(crs="NTG", name="Nottingham", lat=52.9472, lng=-1.1461),
    Station(crs="DBY", name="Derby", lat=52.9164, lng=-1.4631),
    Station(crs="LEI", name="Leicester", lat=52.6314, lng=-1.1250),
    Station(crs="COV", name="Coventry", lat=52.4015, lng=-1.5130),
    Station(crs="WVH", name="Wolverhampton", lat=52.5879, lng=-2.1202),
    Station(crs="PRE", name="Preston", lat=53.7563, lng=-2.7083),
    Station(crs="LAN", name="Lancaster", lat=54.0481, lng=-2.8081),
    Station(crs="CAR", name="Carlisle", lat=54.8908, lng=-2.9333),
    Station(crs="ABD", name="Aberdeen", lat=57.1434, lng=-2.0976),
    Station(crs="INV", name="Inverness", lat=57.4800, lng=-4.2239),
    Station(crs="BFN", name="Belfast (Lanyon Place)", lat=54.5967, lng=-5.9156),
)


__all__ = ["PRIVATE_STATIONS"]
