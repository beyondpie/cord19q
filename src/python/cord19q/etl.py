"""
Transforms raw CORD-19 data into an articles.sqlite SQLite database.
"""

import csv
import hashlib
import json
import os.path
import sqlite3
import sys

from dateutil import parser
from nltk.tokenize import sent_tokenize

# Articles schema
ARTICLES = {
    'Id': 'TEXT PRIMARY KEY',
    'Source': 'TEXT',
    'Published': 'DATETIME',
    'Publication': "TEXT",
    'Authors': 'TEXT',
    'Title': 'TEXT',
    'Tags': 'TEXT',
    'Reference': 'TEXT'
}

# Sections schema
SECTIONS = {
    'Id': 'INTEGER PRIMARY KEY',
    'Article': 'TEXT',
    'Text': 'TEXT',
    'Tags': 'TEXT'
}

# SQL statements
CREATE_TABLE = "CREATE TABLE IF NOT EXISTS {table} ({fields})"
INSERT_ROW = "INSERT INTO {table} ({columns}) VALUES ({values})"

class Etl(object):
    """
    Transforms raw csv and json files into an articles.sqlite SQLite database.
    """

    @staticmethod
    def init(output):
        """
        Connects initializes a new output SQLite database.

        Args:
            output: output directory, if None uses default path

        Returns:
            connection to new SQLite database
        """

        # Default path if not provided
        if not output:
            output = os.path.join(os.path.expanduser("~"), ".cord19", "models")

        # Create if output path doesn't exist
        os.makedirs(output, exist_ok=True)

        # Output database file
        dbfile = os.path.join(output, "articles.sqlite")

        # Delete existing file
        if os.path.exists(dbfile):
            os.remove(dbfile)

        # Create output database
        db = sqlite3.connect(dbfile)

        # Create articles table
        Etl.create(db, ARTICLES, "articles")

        # Create sections table
        Etl.create(db, SECTIONS, "sections")

        return db

    @staticmethod
    def create(db, table, name):
        """
        Creates a SQLite table.

        Args:
            db: database connection
            table: table schema
            name: table name
        """

        columns = ['{0} {1}'.format(name, ctype) for name, ctype in table.items()]
        create = CREATE_TABLE.format(table=name, fields=", ".join(columns))

        # pylint: disable=W0703
        try:
            db.execute(create)
        except Exception as e:
            print(create)
            print("Failed to create table: " + e)

    @staticmethod
    def insert(db, table, name, row):
        """
        Builds and inserts a row.

        Args:
            db: database connection
            table: table object
            name: table name
            row: row to insert
        """

        # Build insert prepared statement
        columns = [name for name, _ in table.items()]
        insert = INSERT_ROW.format(table=name,
                                   columns=", ".join(columns),
                                   values=("?, " * len(columns))[:-2])

        try:
            # Execute insert statement
            db.execute(insert, Etl.values(table, row, columns))
        # pylint: disable=W0703
        except Exception as ex:
            print("Error inserting row: {}".format(row), ex)

    @staticmethod
    def values(table, row, columns):
        """
        Formats and converts row into database types based on table schema.

        Args:
            table: table schema
            row: row tuple
            columns: column names

        Returns:
            Database schema formatted row tuple
        """

        values = []
        for x, column in enumerate(columns):
            # Get value
            value = row[x]

            if table[column].startswith('INTEGER'):
                values.append(int(value) if value else 0)
            elif table[column] == 'BOOLEAN':
                values.append(1 if value == "TRUE" else 0)
            else:
                values.append(value)

        return values

    @staticmethod
    def getIds(row, ids):
        """
        Gets ids for this row. Builds one from the title if no body content is available.

        Args:
            row: input row
            ids: list of generated ids

        Returns:
            list of sha1 hash ids
        """

        # Use sha1 provided, if available
        uids = row["sha"].split("; ") if row["sha"] else None
        if not uids:
            # Fallback to sha1 of title
            uid = hashlib.sha1(row["title"].encode("utf-8")).hexdigest()

            # Reject duplicate generated ids
            if uid in ids:
                return None

            # Add to generated id list and set to list
            ids.add(uid)
            uids = [uid]

        return uids

    @staticmethod
    def getDate(row):
        """
        Parses the publish date from the input row.

        Args:
            row: input row

        Returns:
            publish date
        """

        date = row["publish_time"]

        if date:
            try:
                if date.isdigit() and len(date) == 4:
                    # Default entries with just year to Jan 1
                    date += "-01-01"

                return parser.parse(date)

            # pylint: disable=W0702
            except:
                # Skip parsing errors
                return None

        return None

    @staticmethod
    def getTags(sections):
        """
        Searches input sections for matching keywords. If found, returns the keyword tag.

        Args:
            sections: list of text sections

        Returns:
            tags
        """

        keywords = ["2019-ncov", "2019 novel coronavirus", "coronavirus 2019", "coronavirus disease 19", "covid-19", "covid 19", "ncov-2019",
                    "sars-cov-2", "wuhan coronavirus", "wuhan pneumonia", "wuhan virus"]

        tags = None
        for text in sections:
            if any(x in text.lower() for x in keywords):
                tags = "COVID-19"

        return tags

    @staticmethod
    def getReference(row):
        """
        Builds a reference link.

        Args:
            row: input row

        Returns:
            resolved reference link
        """

        # Resolve doi link
        return ("https://doi.org/" + row["doi"]) if row["doi"] else None

    @staticmethod
    def read(directory, row):
        """
        Reads body text for a given row. Body text is returned as a list of sections.

        Args:
            directory: input directory
            row: input row

        Returns:
            list of sections
        """

        sections = []

        # Get ids and subset
        uids = row["sha"].split("; ") if row["sha"] else None
        subset = row["full_text_file"]

        if uids and subset:
            for uid in uids:
                # Build article path. Path has subset directory twice.
                article = os.path.join(directory, subset, subset, uid + ".json")

                try:
                    with open(article) as jfile:
                        data = json.load(jfile)

                        # Extract text from each section
                        for section in data["body_text"]:
                            # Filter out boilerplate text from indexing
                            if not "COVID-19 resource centre remains active" in section["text"]:
                                # Split text into sentences and add to sections
                                sections.extend(sent_tokenize(section["text"]))

                # pylint: disable=W0703
                except Exception as ex:
                    print("Error processing text file: {}".format(article), ex)

        return sections

    @staticmethod
    def run(directory, output):
        """
        Main execution method.

        Args:
            directory: input directory
            output: output directory path
        """

        print("Building articles.sqlite from {}".format(directory))

        # Initialize database
        db = Etl.init(output)

        # Article and section indices
        aindex = 0
        sindex = 0

        # List of generated ids, used for documents without full content
        ids = set()

        with open(os.path.join(directory, "metadata.csv"), mode="r") as csvfile:
            for row in csv.DictReader(csvfile):
                # Get uids, skip row if None, which only happens for duplicate generated ids
                uids = Etl.getIds(row, ids)
                if uids:
                    # Published date
                    date = Etl.getDate(row)

                    # Get text sections
                    sections = [row["title"]] + Etl.read(directory, row)

                    # Get tags
                    tags = Etl.getTags(sections)

                    # Article row - id, source, published, publication, authors, title, tags, reference
                    article = (uids[0], row["source_x"], date, row["journal"], row["authors"], row["title"], tags, Etl.getReference(row))
                    Etl.insert(db, ARTICLES, "articles", article)

                    # Increment number of articles processed
                    aindex += 1
                    if aindex % 1000 == 0:
                        print("Inserted {} articles".format(aindex))

                    # Add each text section
                    for text in sections:
                        # id, article, text, tags
                        Etl.insert(db, SECTIONS, "sections", (sindex, uids[0], text, tags))
                        sindex += 1

        print("Total articles inserted: {}".format(aindex))

        # Commit changes and close
        db.commit()
        db.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        Etl.run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
