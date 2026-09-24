"""Spark session: the cluster's own inside Databricks, serverless Databricks Connect locally."""

import os


def get_spark():
    if "DATABRICKS_RUNTIME_VERSION" in os.environ:
        from pyspark.sql import SparkSession

        return SparkSession.builder.getOrCreate()
    from databricks.connect import DatabricksSession  # pip install databricks-connect

    return DatabricksSession.builder.serverless().getOrCreate()


def set_comments(spark, table: str, table_comment: str, columns: dict[str, str]) -> None:
    """Table and column descriptions live in Unity Catalog, where Genie reads them."""

    def q(text: str) -> str:
        return text.replace("'", "\\'")

    spark.sql(f"COMMENT ON TABLE {table} IS '{q(table_comment)}'")
    for col, comment in columns.items():
        spark.sql(f"ALTER TABLE {table} ALTER COLUMN {col} COMMENT '{q(comment)}'")
