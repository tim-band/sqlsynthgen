"""This file was auto-generated by sqlsynthgen but can be edited manually."""
from mimesis import Generic
from mimesis.locales import Locale
from sqlsynthgen.providers import ForeignKeyProvider

generic = Generic(locale=Locale.EN)
generic.add_provider(ForeignKeyProvider)


class advance_decisionGenerator:
    def __init__(self, db_connection):
        self.advance_decision_type_id = generic.numeric.integer_number()
        self.status_change_datetime = generic.datetime.datetime()


class mrnGenerator:
    def __init__(self, db_connection):
        self.mrn = generic.text.color()
        self.nhs_number = generic.text.color()
        self.research_opt_out = generic.development.boolean()
        self.source_system = generic.text.color()
        self.stored_from = generic.datetime.datetime()


class lab_sampleGenerator:
    def __init__(self, db_connection):
        self.mrn_id = generic.foreign_key_provider.key(db_connection, "star", "mrn", "mrn_id")
        self.external_lab_number = generic.text.color()
        self.receipt_at_lab_datetime = generic.datetime.datetime()
        self.sample_collection_datetime = generic.datetime.datetime()
        self.specimen_type = generic.text.color()
        self.sample_site = generic.text.color()
        self.collection_method = generic.text.color()
        self.valid_from = generic.datetime.datetime()
        self.stored_from = generic.datetime.datetime()


sorted_generators = [
    advance_decisionGenerator,
    mrnGenerator,
    lab_sampleGenerator,
]
