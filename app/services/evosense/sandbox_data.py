"""SANDBOX DATA. Every record here is synthetic and is marked SANDBOX wherever
it appears. No real person, phone number or email address is in this file:
phones are in the 555-01xx range reserved for fiction, emails end in
`.example`, and mailing addresses use a street name ("Sandbox") that does not
exist. Property addresses are plausible DFW street names with invented house
numbers so the geography logic has something real-shaped to work on.

The dataset is deterministic: the same strategy always discovers the same
properties, so tests and the review walkthrough are repeatable.
"""
from datetime import datetime

Y = datetime.utcnow().year


def _d(years_ago: int, month: int = 6) -> str:
    return "%04d-%02d-15" % (Y - years_ago, month)


# key, address, county, value, mortgage, years owned, owner, mailing, facts
PROPERTIES = [
    # ── the flagship ────────────────────────────────────────────────────────
    dict(ref="SBX-P-1001", street_address="1418 Cedar Springs Rd", city="Dallas", state="TX",
         zip_code="75201", county="Dallas", parcel_apn="00-0741-823-0000", property_type="single_family",
         bedrooms=3, bathrooms=2, square_feet=1650, year_built=1962, value=305000, mortgage=118950,
         last_sale=_d(18), owner="Evelyn R. Harper",
         mailing=("4410 Sandbox Ridge Dr", "Tulsa", "OK", "74105"),
         vacancy=True, tax=True, lat=32.7936, lng=-96.8043),
    # ── high opportunity, skip trace finds nobody ──────────────────────────
    dict(ref="SBX-P-1002", street_address="2611 Glenfield Ave", city="Dallas", state="TX",
         zip_code="75233", county="Dallas", parcel_apn="00-0520-114-0000", property_type="single_family",
         bedrooms=3, bathrooms=1, square_feet=1210, year_built=1958, value=212000, mortgage=0,
         last_sale=_d(27), owner="Walter J. Pruitt",
         mailing=("77 Sandbox Hollow", "Phoenix", "AZ", "85004"),
         vacancy=True, tax=True),
    # ── wrong person journey ────────────────────────────────────────────────
    dict(ref="SBX-P-1003", street_address="4915 Live Oak St", city="Dallas", state="TX",
         zip_code="75204", county="Dallas", parcel_apn="00-0412-907-0000", property_type="single_family",
         bedrooms=2, bathrooms=1, square_feet=1080, year_built=1948, value=268000, mortgage=90000,
         last_sale=_d(21), owner="Gerald M. Whitcomb",
         mailing=("12 Sandbox Ct", "Plano", "TX", "75024"),
         vacancy=True, code_violation=True),
    # ── not now / nurture journey ──────────────────────────────────────────
    dict(ref="SBX-P-1004", street_address="7302 Ferguson Rd", city="Dallas", state="TX",
         zip_code="75228", county="Dallas", parcel_apn="00-0663-218-0000", property_type="single_family",
         bedrooms=3, bathrooms=2, square_feet=1480, year_built=1966, value=246000, mortgage=61000,
         last_sale=_d(16), owner="Loretta A. Kincaid",
         mailing=("905 Sandbox Pkwy", "Austin", "TX", "78701"),
         tax=True, vacancy=True),
    # ── suppressed contact (on the platform suppression list already) ───────
    dict(ref="SBX-P-1005", street_address="3330 Hatcher St", city="Dallas", state="TX",
         zip_code="75210", county="Dallas", parcel_apn="00-0348-020-0000", property_type="single_family",
         bedrooms=2, bathrooms=1, square_feet=980, year_built=1940, value=158000, mortgage=0,
         last_sale=_d(31), owner="Harold D. Benning",
         mailing=("40 Sandbox Way", "Shreveport", "LA", "71101"),
         vacancy=True, tax=True),
    # ── LLC owner that cannot be legitimately resolved ─────────────────────
    dict(ref="SBX-P-1006", street_address="2124 S Ervay St", city="Dallas", state="TX",
         zip_code="75215", county="Dallas", parcel_apn="00-0101-355-0000", property_type="single_family",
         bedrooms=3, bathrooms=2, square_feet=1390, year_built=1925, value=331000, mortgage=120000,
         last_sale=_d(12), owner="Oak Cliff Holdings LLC",
         mailing=("1 Sandbox Plaza Ste 400", "Wilmington", "DE", "19801"),
         vacancy=True, code_violation=True),
    # ── provider failure (primary skip trace times out) ────────────────────
    dict(ref="SBX-P-1007", street_address="1805 Nolte Dr", city="Dallas", state="TX",
         zip_code="75208", county="Dallas", parcel_apn="00-0277-601-0000", property_type="single_family",
         bedrooms=3, bathrooms=2, square_feet=1540, year_built=1955, value=289000, mortgage=70000,
         last_sale=_d(19), owner="Marianne F. Okafor",
         mailing=("300 Sandbox Ave", "Denver", "CO", "80202"),
         vacancy=True, tax=True),
    # ── ambiguous identity: same number+street, one record has a unit ──────
    dict(ref="SBX-P-1008", street_address="5530 Bonnie View Rd", city="Dallas", state="TX",
         zip_code="75241", county="Dallas", parcel_apn="00-0980-440-0000", property_type="single_family",
         bedrooms=3, bathrooms=1, square_feet=1150, year_built=1961, value=149000, mortgage=40000,
         last_sale=_d(14), owner="Clarence P. Dade",
         mailing=("5530 Bonnie View Rd", "Dallas", "TX", "75241"), tax=True),
    # ── one owner, six Tarrant properties ──────────────────────────────────
    *[dict(ref="SBX-P-12%02d" % i, street_address=addr, city=city, state="TX", zip_code=z,
           county="Tarrant", parcel_apn="TR-%05d" % (4400 + i), property_type="single_family",
           bedrooms=3, bathrooms=2, square_feet=1300 + i * 40, year_built=1970 + i, value=val,
           mortgage=int(val * 0.35), last_sale=_d(13 + i), owner="Raymond T. Castillo",
           mailing=("2200 Sandbox Ranch Rd", "Midland", "TX", "79701"),
           vacancy=(i % 2 == 0), tax=(i == 3))
      for i, (addr, city, z, val) in enumerate([
          ("3809 Wenonah Dr", "Fort Worth", "76109", 262000),
          ("6120 Wedgwood Dr", "Fort Worth", "76133", 214000),
          ("2917 Hemphill St", "Fort Worth", "76110", 198000),
          ("1405 E Magnolia Ave", "Fort Worth", "76104", 236000),
          ("4508 Birchman Ave", "Fort Worth", "76107", 318000),
          ("909 W Division St", "Arlington", "76012", 205000),
      ], start=1)],
    # ── budget-blocked candidates (Tarrant probate strategy) ───────────────
    dict(ref="SBX-P-1301", street_address="3102 Avenue J", city="Fort Worth", state="TX",
         zip_code="76105", county="Tarrant", parcel_apn="TR-06601", property_type="single_family",
         bedrooms=2, bathrooms=1, square_feet=960, year_built=1947, value=141000, mortgage=0,
         last_sale=_d(35), owner="Estate of Bernice L. Tolliver",
         mailing=("3102 Avenue J", "Fort Worth", "TX", "76105"), probate=True, vacancy=True),
    dict(ref="SBX-P-1302", street_address="5316 Wellesley Ave", city="Fort Worth", state="TX",
         zip_code="76107", county="Tarrant", parcel_apn="TR-06602", property_type="single_family",
         bedrooms=3, bathrooms=2, square_feet=1440, year_built=1952, value=284000, mortgage=0,
         last_sale=_d(40), owner="Estate of Clifford A. Mercer",
         mailing=("88 Sandbox Bend", "Little Rock", "AR", "72201"), probate=True, vacancy=True),
    dict(ref="SBX-P-1303", street_address="2710 Stadium Dr", city="Fort Worth", state="TX",
         zip_code="76109", county="Tarrant", parcel_apn="TR-06603", property_type="single_family",
         bedrooms=3, bathrooms=2, square_feet=1600, year_built=1959, value=339000, mortgage=0,
         last_sale=_d(33), owner="Estate of Dolores V. Ybarra",
         mailing=("14 Sandbox Loop", "El Paso", "TX", "79901"), probate=True, tax=True),
    # ── volume: ordinary properties, most of which should be rejected ──────
    *[dict(ref="SBX-P-14%02d" % i, street_address=addr, city=city, state="TX", zip_code=z,
           county=cty, parcel_apn="VX-%05d" % (7700 + i), property_type=ptype,
           bedrooms=3, bathrooms=2, square_feet=1400 + i * 25, year_built=1980 + i,
           value=val, mortgage=mort, last_sale=_d(yrs), owner=owner,
           mailing=(maddr, mcity, "TX", mzip), vacancy=vac, tax=tax)
      for i, (addr, city, z, cty, ptype, val, mort, yrs, owner, maddr, mcity, mzip, vac, tax) in enumerate([
          ("8811 Forest Hills Blvd", "Dallas", "75218", "Dallas", "single_family", 612000, 350000, 4, "Nadia K. Brandt", "8811 Forest Hills Blvd", "Dallas", "75218", False, False),
          ("4122 Lively Ln", "Dallas", "75220", "Dallas", "single_family", 402000, 280000, 6, "Tomas G. Aldana", "4122 Lively Ln", "Dallas", "75220", False, False),
          ("1930 Kessler Pkwy", "Dallas", "75208", "Dallas", "single_family", 455000, 150000, 11, "Priya N. Raman", "1930 Kessler Pkwy", "Dallas", "75208", False, False),
          ("6402 Lakeshore Dr", "Dallas", "75214", "Dallas", "single_family", 890000, 400000, 8, "Grant W. Ellery", "6402 Lakeshore Dr", "Dallas", "75214", False, False),
          ("2211 Canada Dr", "Dallas", "75212", "Dallas", "single_family", 178000, 150000, 3, "Rosa M. Quintero", "2211 Canada Dr", "Dallas", "75212", False, False),
          ("714 N Clinton Ave", "Dallas", "75208", "Dallas", "duplex", 390000, 120000, 15, "Kenji A. Moreau", "55 Sandbox Ct", "Irving", "75039", True, False),
          ("3421 Sappington Pl", "Dallas", "75227", "Dallas", "single_family", 199000, 20000, 22, "Opal J. Fenwick", "3421 Sappington Pl", "Dallas", "75227", False, True),
          ("5102 Bryce Ave", "Fort Worth", "76107", "Tarrant", "single_family", 540000, 300000, 5, "Lucas B. Haddad", "5102 Bryce Ave", "Fort Worth", "76107", False, False),
          ("2932 Ryan Ave", "Fort Worth", "76110", "Tarrant", "single_family", 221000, 95000, 12, "Irene C. Vasquez", "700 Sandbox Mesa", "Albuquerque", "76110", False, False),
          ("1609 Carter Ave", "Fort Worth", "76103", "Tarrant", "single_family", 176000, 60000, 17, "Delbert S. Rowe", "1609 Carter Ave", "Fort Worth", "76103", True, False),
          ("4400 Harley Ave", "Fort Worth", "76107", "Tarrant", "condo", 260000, 100000, 9, "Maren L. Sato", "4400 Harley Ave", "Fort Worth", "76107", False, False),
          ("1200 Sandy Ln", "Arlington", "76012", "Tarrant", "single_family", 305000, 260000, 2, "Bryce D. Lowell", "1200 Sandy Ln", "Arlington", "76012", False, False),
      ], start=1)],
]

# Vacancy and tax providers report the same houses in their OWN formats, with
# their OWN reference ids - this is what identity resolution has to reconcile.
VACANCY_FORMAT = {
    "SBX-P-1001": dict(street_address="1418 CEDAR SPRINGS ROAD", city="DALLAS", state="TX",
                       zip_code="75201-2702"),
}
TAX_FORMAT = {
    "SBX-P-1001": dict(street_address="1418 Cedar Springs Rd.", city="Dallas", state="TX",
                       zip_code=None, county="Dallas County"),
    # The tax roll lists this parcel against a unit the property record does
    # not carry: same house number and street, unit on one side only.
    "SBX-P-1008": dict(street_address="5530 Bonnie View Rd Unit B", city="Dallas",
                       state="TX", zip_code="75241", county="Dallas", parcel_apn=None),
}

# Contact enrichment, keyed by owner name. `behaviour` drives the journeys.
CONTACTS = {
    "Evelyn R. Harper": dict(phones=[("+12145550142", "mobile", 88, "sandbox:utility+telecom")],
                             emails=["eharper@harper-family.example"],
                             mailing=("4410 Sandbox Ridge Dr", "Tulsa", "OK", "74105")),
    "Walter J. Pruitt": dict(behaviour="no_match"),
    "Gerald M. Whitcomb": dict(phones=[("+12145550177", "mobile", 71, "sandbox:telecom")],
                               mailing=("12 Sandbox Ct", "Plano", "TX", "75024")),
    "Loretta A. Kincaid": dict(phones=[("+15125550119", "mobile", 84, "sandbox:utility+telecom")],
                               mailing=("905 Sandbox Pkwy", "Austin", "TX", "78701")),
    "Harold D. Benning": dict(phones=[("+13185550163", "mobile", 80, "sandbox:telecom")],
                              mailing=("40 Sandbox Way", "Shreveport", "LA", "71101")),
    "Oak Cliff Holdings LLC": dict(behaviour="llc_agent",
                                   phones=[("+13025550188", "landline", 35, "sandbox:registered_agent")],
                                   person=("Registered agent (unnamed)", "agent")),
    "Marianne F. Okafor": dict(behaviour="fail"),
    "Clarence P. Dade": dict(phones=[("+12145550131", "mobile", 76, "sandbox:telecom")],
                             mailing=("5530 Bonnie View Rd", "Dallas", "TX", "75241")),
    "Raymond T. Castillo": dict(phones=[("+14325550155", "mobile", 86, "sandbox:utility+telecom")],
                                mailing=("2200 Sandbox Ranch Rd", "Midland", "TX", "79701")),
    "Estate of Bernice L. Tolliver": dict(phones=[("+18175550124", "mobile", 62, "sandbox:telecom")],
                                          person=("Darnell Tolliver", "heir")),
    "Estate of Clifford A. Mercer": dict(phones=[("+15015550149", "mobile", 66, "sandbox:telecom")],
                                         person=("Janet Mercer-Hollis", "executor")),
    "Estate of Dolores V. Ybarra": dict(phones=[("+19155550117", "mobile", 64, "sandbox:telecom")],
                                        person=("Ruben Ybarra", "heir")),
    "Kenji A. Moreau": dict(phones=[("+14695550150", "mobile", 79, "sandbox:telecom")]),
    "Opal J. Fenwick": dict(phones=[("+12145550166", "landline", 70, "sandbox:telecom")]),
    "Delbert S. Rowe": dict(phones=[("+18175550138", "mobile", 74, "sandbox:telecom")]),
}

# Numbers the phone-validation sandbox reports as invalid / landline.
PHONE_VALIDATION = {
    "+13025550188": ("landline", "valid"),
    "+12145550166": ("landline", "valid"),
}
