"""Entry point for the Databricks pipeline job: anr-pipeline <step> [options]."""

import argparse
import logging

from anr_radar import config, quality
from anr_radar.spark import get_spark
from anr_radar.tables import Tables
from anr_radar.transform import bronze, gold, silver

STEPS = ("bronze", "silver", "gold", "quality")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="A&R Radar medallion pipeline")
    parser.add_argument("step", choices=(*STEPS, "all"))
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--prefix", default="anr_", help="schema prefix, e.g. dev_anr_")
    parser.add_argument("--landing-root", default=config.DEFAULT_LANDING_ROOT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    spark = get_spark()
    t = Tables(args.catalog, args.prefix)
    for step in STEPS if args.step == "all" else (args.step,):
        logging.info("=== %s ===", step)
        if step == "bronze":
            bronze.run(spark, t, args.landing_root)
        elif step == "silver":
            silver.run(spark, t)
        elif step == "gold":
            gold.run(spark, t)
        else:
            quality.run(spark, t)


if __name__ == "__main__":
    main()
