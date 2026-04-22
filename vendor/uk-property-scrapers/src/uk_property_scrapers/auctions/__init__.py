"""Parsers for UK property auction houses.

Each sub-module is a pure function mapping a raw-payload :class:`dict`
(auction house site) or :class:`str` (HTML) to canonical
:class:`~uk_property_scrapers.AuctionLot` models. I/O, pagination, proxy
rotation, and anti-bot concerns live in the caller (the ``uk-auctions``
Apify actor or the agent).

Modules
-------

* :mod:`~uk_property_scrapers.auctions.allsop` — Allsop
  (JSON feed at ``/api/search`` + ``/api/auctions/<uuid>``).
* :mod:`~uk_property_scrapers.auctions.auction_house` — Auction House UK
  (regional HTML catalogues at ``/{branch}/auction/lots/{id}``).
* :mod:`~uk_property_scrapers.auctions.savills` — Savills Auctions
  (HTML catalogue + ``/upcoming-auctions`` discovery).
* :mod:`~uk_property_scrapers.auctions.iamsold` — iamsold (rolling
  modern-method feed at ``/available-properties/``).
"""

from uk_property_scrapers.auctions import (
    allsop,
    auction_house,
    iamsold,
    savills,
)

__all__ = ["allsop", "auction_house", "iamsold", "savills"]
