#!/usr/bin/env python3
"""Generate the city dataset from probe findings.

Coverage comes DIRECTLY from the breadth probe's JSON output — never
editorial, never assumed. This table only supplies cultural data
(keywords, vibes, phrases, aliases) for destinations the probe measured.

Usage:
    python tools/generate_dataset.py --probe probe/results_SIN_20260918.json \
        --out data/cities.json

A city the probe never measured is a build FAILURE, not an 'UNPROBED'
row: coverage must be measured, never assumed.

This is a BUILD-TIME tool. It is fenced into tools/ and never imported
from src/. The committed data/cities.json is what the runtime reads.
"""

import argparse
import json
import pathlib
import re
import sys


# Candidate city metadata — assembled from travel knowledge, not probe output.
# The probe decides atlasCoverage; this table only supplies the cultural data.
CITY_CATALOG = [
    {
        "cityId": "DPS", "cityName": "Bali", "country": "Indonesia",
        "keywords": ["beach", "temple", "rice terrace", "surfing", "yoga", "nightlife", "seafood"],
        "vibes": ["tropical", "laid-back", "spiritual", "party"],
        "phrases": ["stunning beaches and warm turquoise water", "ancient Hindu temples in lush jungle",
                     "world-class surfing and beach clubs", "cheap seafood and vibrant nightlife",
                     "rice terraces and yoga retreats"],
        "aliases": ["bali", "indonesia", "indonesian", "denpasar", "dps"],
    },
    {
        "cityId": "BKK", "cityName": "Bangkok", "country": "Thailand",
        "keywords": ["street food", "temples", "nightlife", "shopping", "malls", "markets", "pad thai"],
        "vibes": ["buzzy", "chaotic", "foodie", "affordable"],
        "phrases": ["incredible street food on every corner", "golden temples and floating markets",
                     "wild nightlife and rooftop bars", "huge shopping malls and night bazaars"],
        "aliases": ["bangkok", "thailand", "thai", "bkk"],
    },
    {
        "cityId": "CNX", "cityName": "Chiang Mai", "country": "Thailand",
        "keywords": ["temples", "mountains", "night market", "street food", "trekking", "coffee"],
        "vibes": ["laid-back", "cultural", "foodie", "chill"],
        "phrases": ["ancient temples in the old city", "mountain trekking and elephant sanctuaries",
                     "famous night bazaar and street food", "specialty coffee and digital nomad scene"],
        "aliases": ["chiang mai", "thailand", "thai", "cnx", "northern thailand"],
    },
    {
        "cityId": "HKT", "cityName": "Phuket", "country": "Thailand",
        "keywords": ["beach", "island", "seafood", "diving", "nightlife"],
        "vibes": ["laid-back", "tropical", "party", "budget-friendly"],
        "phrases": ["long white-sand beaches and warm water", "cheap seafood grills on the sand",
                     "island hopping and day boats"],
        "aliases": ["phuket", "thailand", "thai", "hkt", "andaman"],
    },
    {
        "cityId": "KUL", "cityName": "Kuala Lumpur", "country": "Malaysia",
        "keywords": ["street food", "shopping", "towers", "hawker", "multicultural", "nasi lemak"],
        "vibes": ["modern", "foodie", "affordable", "diverse"],
        "phrases": ["incredible hawker food and street stalls", "iconic Petronas Twin Towers skyline",
                     "multicultural food scene with Malay Chinese Indian"],
        "aliases": ["kuala lumpur", "malaysia", "malaysian", "kul", "kl"],
    },
    {
        "cityId": "PEN", "cityName": "Penang", "country": "Malaysia",
        "keywords": ["street food", "heritage", "hawker", "murals", "char kway teow", "laksa"],
        "vibes": ["foodie", "cultural", "laid-back", "historic"],
        "phrases": ["best street food in Southeast Asia", "UNESCO heritage Georgetown old town",
                     "famous char kway teow and assam laksa"],
        "aliases": ["penang", "georgetown", "malaysia", "malaysian", "pen"],
    },
    {
        "cityId": "ICN", "cityName": "Seoul", "country": "South Korea",
        "keywords": ["kimchi", "kpop", "street food", "palaces", "shopping", "bbq", "skincare"],
        "vibes": ["modern", "trendy", "foodie", "cultural"],
        "phrases": ["K-pop culture and trendy Gangnam district", "incredible Korean BBQ and street food",
                     "world-famous kimchi and Korean fried chicken"],
        "aliases": ["seoul", "south korea", "korea", "korean", "kimchi", "kpop", "icn"],
    },
    {
        "cityId": "PUS", "cityName": "Busan", "country": "South Korea",
        "keywords": ["beach", "seafood", "fish market", "kimchi", "hot springs", "temple"],
        "vibes": ["coastal", "foodie", "laid-back", "cultural"],
        "phrases": ["beautiful beaches and Jagalchi fish market", "fresh seafood and Korean beach culture"],
        "aliases": ["busan", "south korea", "korea", "korean", "pus"],
    },
    {
        "cityId": "NRT", "cityName": "Tokyo", "country": "Japan",
        "keywords": ["sushi", "ramen", "anime", "shopping", "temples", "technology", "onsen"],
        "vibes": ["modern", "cultural", "foodie", "tech-forward"],
        "phrases": ["the best sushi and ramen in the world", "anime culture and Akihabara electronics",
                     "ancient temples next to neon-lit streets"],
        "aliases": ["tokyo", "japan", "japanese", "sushi", "ramen", "anime", "nrt"],
    },
    {
        "cityId": "KIX", "cityName": "Osaka", "country": "Japan",
        "keywords": ["street food", "takoyaki", "okonomiyaki", "nightlife", "shopping", "castle"],
        "vibes": ["foodie", "buzzy", "fun", "affordable"],
        "phrases": ["the kitchen of Japan with incredible street food",
                     "famous takoyaki and okonomiyaki", "Dotonbori nightlife and neon signs"],
        "aliases": ["osaka", "japan", "japanese", "kix", "kansai"],
    },
    {
        "cityId": "FUK", "cityName": "Fukuoka", "country": "Japan",
        "keywords": ["ramen", "street food", "shrines", "markets", "onsen"],
        "vibes": ["foodie", "laid-back", "cultural"],
        "phrases": ["the birthplace of tonkotsu ramen", "ancient shrines and relaxing onsen"],
        "aliases": ["fukuoka", "japan", "japanese", "fuk", "hakata", "tonkotsu"],
    },
    {
        "cityId": "TPE", "cityName": "Taipei", "country": "Taiwan",
        "keywords": ["night market", "street food", "bubble tea", "hot springs", "temples", "hiking"],
        "vibes": ["foodie", "cultural", "laid-back", "friendly"],
        "phrases": ["legendary night markets with endless street food",
                     "birthplace of bubble tea", "natural hot springs in Beitou district"],
        "aliases": ["taipei", "taiwan", "taiwanese", "tpe", "bubble tea"],
    },
    {
        "cityId": "HKG", "cityName": "Hong Kong", "country": "China",
        "keywords": ["dim sum", "skyline", "shopping", "harbor", "street food", "hiking"],
        "vibes": ["modern", "foodie", "buzzy", "cosmopolitan"],
        "phrases": ["world-famous dim sum and Cantonese cuisine",
                     "spectacular Victoria Harbour skyline"],
        "aliases": ["hong kong", "hongkong", "hkg", "cantonese"],
    },
    {
        "cityId": "SGN", "cityName": "Ho Chi Minh City", "country": "Vietnam",
        "keywords": ["street food", "pho", "coffee", "history", "war museum", "markets"],
        "vibes": ["buzzy", "foodie", "historic", "affordable"],
        "phrases": ["incredible pho and banh mi on every street",
                     "strong Vietnamese coffee culture"],
        "aliases": ["ho chi minh", "saigon", "vietnam", "vietnamese", "sgn"],
    },
    {
        "cityId": "HAN", "cityName": "Hanoi", "country": "Vietnam",
        "keywords": ["street food", "pho", "old quarter", "coffee", "lake", "history"],
        "vibes": ["cultural", "foodie", "historic", "laid-back"],
        "phrases": ["the birthplace of pho and egg coffee",
                     "charming old quarter with narrow streets"],
        "aliases": ["hanoi", "vietnam", "vietnamese", "han"],
    },
    {
        "cityId": "DAD", "cityName": "Da Nang", "country": "Vietnam",
        "keywords": ["beach", "banh mi", "dragon bridge", "marble mountains"],
        "vibes": ["coastal", "laid-back", "affordable"],
        "phrases": ["beautiful beaches along the central coast"],
        "aliases": ["da nang", "danang", "vietnam", "vietnamese", "dad"],
    },
    {
        "cityId": "MNL", "cityName": "Manila", "country": "Philippines",
        "keywords": ["street food", "nightlife", "history", "malls", "adobo"],
        "vibes": ["buzzy", "affordable", "foodie", "chaotic"],
        "phrases": ["vibrant street food and night markets",
                     "Spanish colonial history and old churches"],
        "aliases": ["manila", "philippines", "filipino", "mnl"],
    },
    {
        "cityId": "CEB", "cityName": "Cebu", "country": "Philippines",
        "keywords": ["beach", "diving", "whale shark", "island", "seafood"],
        "vibes": ["tropical", "adventure", "laid-back", "budget-friendly"],
        "phrases": ["swimming with whale sharks in Oslob", "stunning island beaches and diving"],
        "aliases": ["cebu", "philippines", "filipino", "ceb"],
    },
    {
        "cityId": "CGK", "cityName": "Jakarta", "country": "Indonesia",
        "keywords": ["street food", "malls", "history", "satay", "rendang"],
        "vibes": ["buzzy", "modern", "chaotic", "foodie"],
        "phrases": ["massive city with incredible street food"],
        "aliases": ["jakarta", "indonesia", "indonesian", "cgk"],
    },
    {
        "cityId": "SUB", "cityName": "Surabaya", "country": "Indonesia",
        "keywords": ["street food", "history", "volcano", "seafood"],
        "vibes": ["buzzy", "affordable", "historic"],
        "phrases": ["gateway to Mount Bromo volcano"],
        "aliases": ["surabaya", "indonesia", "indonesian", "sub", "java"],
    },
    {
        "cityId": "PER", "cityName": "Perth", "country": "Australia",
        "keywords": ["beach", "wine", "nature", "surfing", "outdoors", "wildlife"],
        "vibes": ["laid-back", "outdoorsy", "sunny", "relaxed"],
        "phrases": ["pristine beaches and sunny weather", "world-class wine regions nearby"],
        "aliases": ["perth", "australia", "australian", "per", "western australia"],
    },
    {
        "cityId": "CMB", "cityName": "Colombo", "country": "Sri Lanka",
        "keywords": ["spices", "tea", "beach", "temples", "curry", "colonial"],
        "vibes": ["cultural", "tropical", "historic", "foodie"],
        "phrases": ["aromatic spices and Ceylon tea plantations",
                     "beautiful beaches and ancient temples"],
        "aliases": ["colombo", "sri lanka", "sri lankan", "sri", "cmb", "ceylon"],
    },
    {
        "cityId": "BOM", "cityName": "Mumbai", "country": "India",
        "keywords": ["street food", "bollywood", "markets", "chai", "vada pav"],
        "vibes": ["chaotic", "vibrant", "foodie", "cultural"],
        "phrases": ["vibrant street food and vada pav everywhere"],
        "aliases": ["mumbai", "bombay", "india", "indian", "bom"],
    },
    {
        "cityId": "DEL", "cityName": "Delhi", "country": "India",
        "keywords": ["street food", "history", "monuments", "spices", "chaat"],
        "vibes": ["historic", "chaotic", "foodie", "cultural"],
        "phrases": ["ancient monuments and Mughal architecture"],
        "aliases": ["delhi", "new delhi", "india", "indian", "del"],
    },
    {
        "cityId": "PNH", "cityName": "Phnom Penh", "country": "Cambodia",
        "keywords": ["temples", "history", "street food", "river", "markets"],
        "vibes": ["historic", "affordable", "cultural"],
        "phrases": ["Royal Palace and ancient Khmer temples"],
        "aliases": ["phnom penh", "cambodia", "cambodian", "pnh", "khmer"],
    },
    {
        "cityId": "SYD", "cityName": "Sydney", "country": "Australia",
        "keywords": ["harbor", "beach", "opera house", "surfing", "brunch"],
        "vibes": ["cosmopolitan", "outdoorsy", "sunny", "modern"],
        "phrases": ["iconic Opera House and Harbour Bridge",
                     "world-famous Bondi Beach and surfing"],
        "aliases": ["sydney", "australia", "australian", "syd", "bondi"],
    },
    {
        "cityId": "DXB", "cityName": "Dubai", "country": "UAE",
        "keywords": ["luxury", "shopping", "skyscrapers", "desert", "brunch"],
        "vibes": ["luxury", "modern", "cosmopolitan"],
        "phrases": ["towering skyscrapers and luxury shopping",
                     "desert safaris and gold souks"],
        "aliases": ["dubai", "uae", "emirates", "dxb"],
    },
    {
        "cityId": "LOP", "cityName": "Lombok", "country": "Indonesia",
        "keywords": ["beach", "surfing", "volcano", "gili islands", "waterfalls"],
        "vibes": ["tropical", "laid-back", "adventurous"],
        "phrases": ["quieter beaches across from Bali",
                     "Mount Rinjani volcano treks and the Gili islands"],
        "aliases": ["lombok", "indonesia", "indonesian", "lop", "gili"],
    },
    {
        "cityId": "MDC", "cityName": "Manado", "country": "Indonesia",
        "keywords": ["diving", "coral", "bunaken", "seafood", "volcano"],
        "vibes": ["tropical", "adventurous", "off-the-beaten-path"],
        "phrases": ["world-class diving at Bunaken marine park"],
        "aliases": ["manado", "indonesia", "indonesian", "mdc", "bunaken"],
    },
    {
        "cityId": "CEI", "cityName": "Chiang Rai", "country": "Thailand",
        "keywords": ["white temple", "mountains", "temples", "tea plantations"],
        "vibes": ["cultural", "laid-back", "mountain"],
        "phrases": ["the surreal White Temple and mountain villages"],
        "aliases": ["chiang rai", "thailand", "thai", "cei", "white temple"],
    },
    {
        "cityId": "USM", "cityName": "Koh Samui", "country": "Thailand",
        "keywords": ["beach", "island", "resort", "spa", "full moon party"],
        "vibes": ["tropical", "laid-back", "upscale"],
        "phrases": ["palm-lined beaches and island spas in the Gulf of Thailand"],
        "aliases": ["koh samui", "ko samui", "samui", "thailand", "thai", "usm"],
    },
    {
        "cityId": "LGK", "cityName": "Langkawi", "country": "Malaysia",
        "keywords": ["beach", "island", "cable car", "mangrove", "duty free"],
        "vibes": ["tropical", "laid-back", "budget-friendly"],
        "phrases": ["island beaches, cable cars and mangrove tours"],
        "aliases": ["langkawi", "malaysia", "malaysian", "lgk"],
    },
    {
        "cityId": "BKI", "cityName": "Kota Kinabalu", "country": "Malaysia",
        "keywords": ["mount kinabalu", "hiking", "island", "seafood", "orangutan"],
        "vibes": ["adventurous", "outdoorsy", "tropical"],
        "phrases": ["gateway to Mount Kinabalu and Borneo wildlife"],
        "aliases": ["kota kinabalu", "borneo", "malaysia", "malaysian", "bki"],
    },
    {
        "cityId": "KCH", "cityName": "Kuching", "country": "Malaysia",
        "keywords": ["riverfront", "national park", "orangutan", "laksa", "rainforest"],
        "vibes": ["laid-back", "cultural", "outdoorsy"],
        "phrases": ["rainforest national parks and a charming riverfront"],
        "aliases": ["kuching", "sarawak", "borneo", "malaysia", "malaysian", "kch"],
    },
    {
        "cityId": "CRK", "cityName": "Angeles", "country": "Philippines",
        "keywords": ["nightlife", "street food", "pinatubo", "adventure"],
        "vibes": ["buzzy", "budget-friendly", "adventurous"],
        "phrases": ["Mount Pinatubo crater treks near Clark"],
        "aliases": ["angeles", "clark", "philippines", "filipino", "crk"],
    },
    {
        "cityId": "DVO", "cityName": "Davao", "country": "Philippines",
        "keywords": ["durian", "island", "eagle", "nature", "seafood"],
        "vibes": ["laid-back", "tropical", "outdoorsy"],
        "phrases": ["gateway to Samal Island and the Philippine eagle"],
        "aliases": ["davao", "philippines", "filipino", "dvo"],
    },
    {
        "cityId": "CXR", "cityName": "Nha Trang", "country": "Vietnam",
        "keywords": ["beach", "diving", "island", "seafood", "nightlife"],
        "vibes": ["tropical", "party", "budget-friendly"],
        "phrases": ["a beach city with island diving tours"],
        "aliases": ["nha trang", "vietnam", "vietnamese", "cxr"],
    },
    {
        "cityId": "CTS", "cityName": "Sapporo", "country": "Japan",
        "keywords": ["snow", "skiing", "ramen", "beer", "onsen"],
        "vibes": ["cosy", "outdoorsy", "foodie"],
        "phrases": ["powder snow, miso ramen and hot springs in Hokkaido"],
        "aliases": ["sapporo", "hokkaido", "japan", "japanese", "cts"],
    },
    {
        "cityId": "NGO", "cityName": "Nagoya", "country": "Japan",
        "keywords": ["castle", "miso katsu", "shopping", "industry"],
        "vibes": ["modern", "foodie", "laid-back"],
        "phrases": ["Nagoya Castle and the local miso katsu food scene"],
        "aliases": ["nagoya", "japan", "japanese", "ngo"],
    },
    {
        "cityId": "KHH", "cityName": "Kaohsiung", "country": "Taiwan",
        "keywords": ["night market", "harbor", "street food", "temples"],
        "vibes": ["foodie", "laid-back", "coastal"],
        "phrases": ["harbor views and legendary night markets"],
        "aliases": ["kaohsiung", "taiwan", "taiwanese", "khh"],
    },
    {
        "cityId": "SHA", "cityName": "Shanghai", "country": "China",
        "keywords": ["skyline", "dumplings", "shopping", "history", "the bund"],
        "vibes": ["modern", "cosmopolitan", "foodie"],
        "phrases": ["the Bund skyline and xiaolongbao dumplings"],
        "aliases": ["shanghai", "china", "chinese", "sha", "the bund"],
    },
    {
        "cityId": "CAN", "cityName": "Guangzhou", "country": "China",
        "keywords": ["dim sum", "cantonese", "markets", "history"],
        "vibes": ["foodie", "buzzy", "historic"],
        "phrases": ["the home of Cantonese dim sum"],
        "aliases": ["guangzhou", "canton", "china", "chinese", "can"],
    },
    {
        "cityId": "REP", "cityName": "Siem Reap", "country": "Cambodia",
        "keywords": ["angkor wat", "temples", "history", "street food", "markets"],
        "vibes": ["historic", "cultural", "affordable"],
        "phrases": ["gateway to the temples of Angkor Wat"],
        "aliases": ["siem reap", "angkor", "cambodia", "cambodian", "rep", "khmer"],
    },
    {
        "cityId": "MAA", "cityName": "Chennai", "country": "India",
        "keywords": ["temples", "beach", "south indian", "dosa", "classical music"],
        "vibes": ["cultural", "historic", "foodie"],
        "phrases": ["Dravidian temples and South Indian dosa"],
        "aliases": ["chennai", "madras", "india", "indian", "maa", "tamil"],
    },
    {
        "cityId": "BLR", "cityName": "Bengaluru", "country": "India",
        "keywords": ["breweries", "parks", "street food", "tech", "nightlife"],
        "vibes": ["modern", "buzzy", "outdoorsy"],
        "phrases": ["India's tech capital with breweries and parks"],
        "aliases": ["bengaluru", "bangalore", "india", "indian", "blr"],
    },
    {
        "cityId": "HYD", "cityName": "Hyderabad", "country": "India",
        "keywords": ["biryani", "history", "charminar", "markets"],
        "vibes": ["historic", "foodie", "cultural"],
        "phrases": ["the Charminar and legendary Hyderabadi biryani"],
        "aliases": ["hyderabad", "india", "indian", "hyd", "biryani"],
    },
    {
        "cityId": "COK", "cityName": "Kochi", "country": "India",
        "keywords": ["backwaters", "spices", "colonial", "seafood", "houseboat"],
        "vibes": ["tropical", "historic", "laid-back"],
        "phrases": ["Kerala backwaters, spice markets and houseboats"],
        "aliases": ["kochi", "cochin", "kerala", "india", "indian", "cok", "backwaters"],
    },
    {
        "cityId": "MEL", "cityName": "Melbourne", "country": "Australia",
        "keywords": ["coffee", "street art", "sport", "brunch", "laneways"],
        "vibes": ["cultural", "foodie", "cosmopolitan"],
        "phrases": ["laneway coffee culture and street art"],
        "aliases": ["melbourne", "australia", "australian", "mel"],
    },
    {
        "cityId": "BNE", "cityName": "Brisbane", "country": "Australia",
        "keywords": ["river", "outdoors", "beach", "wildlife", "sunny"],
        "vibes": ["laid-back", "outdoorsy", "sunny"],
        "phrases": ["river city gateway to the Gold Coast"],
        "aliases": ["brisbane", "australia", "australian", "bne", "queensland"],
    },
]


def parse_probe(probe_path):
    """Load measured coverage from the breadth probe's JSON output.

    Returns {cityId: 'REACHABLE'|'EMPTY'}. A destination the probe errored
    on is NOT coverage — the probe must be re-run until it answers.
    """
    data = json.loads(pathlib.Path(probe_path).read_text(encoding="utf-8"))

    if data.get("errors"):
        sys.exit("Probe JSON carries %d unanswered destinations (%s). "
                 "Re-run the probe; an error is not coverage." % (
                     len(data["errors"]),
                     [e["dest"] for e in data["errors"]]))

    coverage = {}
    for r in data.get("reachable", []):
        coverage[r["dest"]] = "REACHABLE"
    for dest in data.get("empty", []):
        coverage[dest] = "EMPTY"
    return coverage


def generate(probe_path, out_path):
    """Generate data/cities.json from the catalog and measured coverage."""
    coverage = parse_probe(probe_path)

    cities = []
    for entry in CITY_CATALOG:
        cid = entry["cityId"]
        if cid not in coverage:
            sys.exit("%s was never measured by the probe. Coverage must be "
                     "measured, never assumed — re-run the breadth probe." % cid)
        city = dict(entry)
        city["atlasCoverage"] = coverage[cid]
        cities.append(city)

    result = {"cities": cities}

    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Summary
    reachable = sum(1 for c in cities if c["atlasCoverage"] == "REACHABLE")
    empty = sum(1 for c in cities if c["atlasCoverage"] == "EMPTY")

    print("Generated %s" % out)
    print("  Total:     %d cities" % len(cities))
    print("  REACHABLE: %d" % reachable)
    print("  EMPTY:     %d" % empty)
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate city dataset")
    parser.add_argument("--probe", required=True,
                        help="Probe results JSON from the breadth probe")
    parser.add_argument("--out", default="data/cities.json",
                        help="Output path for cities.json")
    args = parser.parse_args()

    generate(args.probe, args.out)


if __name__ == "__main__":
    main()
