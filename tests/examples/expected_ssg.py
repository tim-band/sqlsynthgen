"""This file was auto-generated by sqlsynthgen but can be edited manually."""
from mimesis import Generic
from mimesis.locales import Locale
from sqlsynthgen.base import FileUploader, TableGenerator
from sqlsynthgen.unique_generator import UniqueGenerator

generic = Generic(locale=Locale.EN_GB)

from sqlsynthgen.providers import BytesProvider

generic.add_provider(BytesProvider)
from sqlsynthgen.providers import ColumnValueProvider

generic.add_provider(ColumnValueProvider)
from sqlsynthgen.providers import TimedeltaProvider

generic.add_provider(TimedeltaProvider)
from sqlsynthgen.providers import TimespanProvider

generic.add_provider(TimespanProvider)
from sqlsynthgen.providers import WeightedBooleanProvider

generic.add_provider(WeightedBooleanProvider)

import tests.examples.example_orm
import row_generators
import story_generators

import yaml

with open("example_stats.yaml", "r", encoding="utf-8") as f:
    SRC_STATS = yaml.unsafe_load(f)

emptyvocabulary_vocab = FileUploader(
    tests.examples.example_orm.EmptyVocabulary.__table__
)
mitigationtype_vocab = FileUploader(tests.examples.example_orm.MitigationType.__table__)
reftounignorabletable_vocab = FileUploader(
    tests.examples.example_orm.RefToUnignorableTable.__table__
)
concepttype_vocab = FileUploader(tests.examples.example_orm.ConceptType.__table__)
concept_vocab = FileUploader(tests.examples.example_orm.Concept.__table__)


class data_type_testGenerator(TableGenerator):
    num_rows_per_pass = 1

    def __init__(self):
        pass

    def __call__(self, dst_db_conn):
        result = {}
        result["myuuid"] = generic.cryptographic.uuid()
        return result


class no_pk_testGenerator(TableGenerator):
    num_rows_per_pass = 1

    def __init__(self):
        pass

    def __call__(self, dst_db_conn):
        result = {}
        result["not_an_id"] = generic.numeric.integer_number()
        return result


class personGenerator(TableGenerator):
    num_rows_per_pass = 2

    def __init__(self):
        pass

    def __call__(self, dst_db_conn):
        result = {}
        result["name"] = generic.person.full_name()
        result["stored_from"] = generic.datetime.datetime(2022, 2022)
        result["research_opt_out"] = row_generators.opt_out(
            generic=generic, count_opt_outs=SRC_STATS["count_opt_outs"]
        )
        return result


class unique_constraint_testGenerator(TableGenerator):
    num_rows_per_pass = 1

    def __init__(self):
        pass
        self.unique_ab_uniq = UniqueGenerator(
            ["a", "b"],
            "unique_constraint_test",
            max_tries=50,
        )
        self.unique_c_uniq = UniqueGenerator(
            ["c"],
            "unique_constraint_test",
            max_tries=50,
        )

    def __call__(self, dst_db_conn):
        result = {}
        result["a"], result["b"] = self.unique_ab_uniq(
            dst_db_conn, ["a", "b"], row_generators.boolean_pair, generic
        )
        result["c"] = self.unique_c_uniq(dst_db_conn, ["c"], generic.text.color)
        return result


class unique_constraint_test2Generator(TableGenerator):
    num_rows_per_pass = 1

    def __init__(self):
        pass
        self.unique_a_uniq2 = UniqueGenerator(
            ["a"],
            "unique_constraint_test2",
            max_tries=50,
        )
        self.unique_abc_uniq2 = UniqueGenerator(
            ["a", "b", "c"],
            "unique_constraint_test2",
            max_tries=50,
        )

    def __call__(self, dst_db_conn):
        result = {}
        result["a"], result["b"], result["c"] = self.unique_abc_uniq2(
            dst_db_conn,
            ["a", "b", "c"],
            self.unique_a_uniq2,
            dst_db_conn,
            ["a", "b", "c"],
            row_generators.unique_constraint_test2,
        )
        return result


class test_entityGenerator(TableGenerator):
    num_rows_per_pass = 1

    def __init__(self):
        pass

    def __call__(self, dst_db_conn):
        result = {}
        result["single_letter_column"] = generic.person.password(1)
        result["vocabulary_entry_id"] = generic.column_value_provider.column_value(
            dst_db_conn, tests.examples.example_orm.EmptyVocabulary, "entry_id"
        )
        return result


class hospital_visitGenerator(TableGenerator):
    num_rows_per_pass = 3

    def __init__(self):
        pass

    def __call__(self, dst_db_conn):
        result = {}
        (
            result["visit_start"],
            result["visit_end"],
            result["visit_duration_seconds"],
        ) = row_generators.timespan_generator(
            generic, 2021, 2022, min_dt_days=1, max_dt_days=30
        )
        result["person_id"] = generic.column_value_provider.column_value(
            dst_db_conn, tests.examples.example_orm.Person, "person_id"
        )
        result["visit_image"] = generic.bytes_provider.bytes()
        result["visit_type_concept_id"] = generic.column_value_provider.column_value(
            dst_db_conn, tests.examples.example_orm.Concept, "concept_id"
        )
        return result


table_generator_dict = {
    "data_type_test": data_type_testGenerator(),
    "no_pk_test": no_pk_testGenerator(),
    "person": personGenerator(),
    "unique_constraint_test": unique_constraint_testGenerator(),
    "unique_constraint_test2": unique_constraint_test2Generator(),
    "test_entity": test_entityGenerator(),
    "hospital_visit": hospital_visitGenerator(),
}


vocab_dict = {
    "empty_vocabulary": emptyvocabulary_vocab,
    "mitigation_type": mitigationtype_vocab,
    "ref_to_unignorable_table": reftounignorabletable_vocab,
    "concept_type": concepttype_vocab,
    "concept": concept_vocab,
}


def run_story_generators_short_story(dst_db_conn):
    return story_generators.short_story(generic)


def run_story_generators_full_row_story(dst_db_conn):
    return story_generators.full_row_story(generic)


def run_story_generators_long_story(dst_db_conn):
    return story_generators.long_story(
        dst_db_conn=dst_db_conn,
        generic=generic,
        count_opt_outs=SRC_STATS["count_opt_outs"],
    )


story_generator_list = [
    {
        "name": run_story_generators_short_story,
        "num_stories_per_pass": 3,
    },
    {
        "name": run_story_generators_full_row_story,
        "num_stories_per_pass": 1,
    },
    {
        "name": run_story_generators_long_story,
        "num_stories_per_pass": 2,
    },
]
