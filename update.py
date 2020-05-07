"""
Get ready for 1.0.0
"""
import ast
import logging
import platform
import os
import re
import sys
import subprocess

from argparse import ArgumentParser
import astor
import ruamel.yaml


logging.basicConfig(format="%(levelname)-10s%(message)s", level=logging.INFO)

SUBDIRS = ("modules", "action")
SPECIALS = {"ospfv2": "OSPFv2", "interfaces": "Interfaces", "static": "Static"}


LICENSE = """
#!/usr/bin/python
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#
"""


def load_py_as_ast(path):
    """
    Load a file as an ast object

    :param path: The full path to the file
    :return: The ast object
    """
    ast_file = astor.code_to_ast.parse_file(path)
    return ast_file


def find_assigment_in_ast(name, ast_file):
    """
    Find an assignment in an ast object

    :param name: The name of the assignement to find
    :param ast_file: The ast object
    :return: A list of ast object matching
    """
    return [
        b for b in ast_file.body if hasattr(b, "targets") and b.targets[0].id == name
    ]


def retrieve_module_name(bodypart):
    """
    Retrieve the module name from a docstring

    :param bodypart: The doctstring extracted from the ast body
    :return: The module name
    """
    if len(bodypart) != 1:
        logging.warning("Failed to find DOCUMENTATION assignment")
        return
    documentation = ruamel.yaml.load(
        bodypart[0].value.value, ruamel.yaml.RoundTripLoader
    )
    name = documentation["module"]
    return name


def update_metdata(bodypart):
    """
    Update the metadata of the module

    :param bodypart: The ANSIBLE_METADATA section
    """
    if len(bodypart) != 1:
        logging.warning("Failed to find ANSIBLE_METADATA assignment")
        return None
    meta = ast.Dict()
    meta.keys = [ast.Constant(s) for s in ["metadata_version", "supported_by"]]
    meta.values = [ast.Constant(s) for s in ["1.1", "Ansible"]]
    bodypart[0].value = meta


def update_documentation(bodypart):
    """
    Update the docuementation of the module

    :param bodypart: The DOCUMENTATION section of the module
    """
    if len(bodypart) != 1:
        logging.warning("Failed to find DOCUMENTATION assignment")
        return
    documentation = ruamel.yaml.load(
        bodypart[0].value.value, ruamel.yaml.RoundTripLoader
    )
    # remove version added
    documentation.pop("version_added", None)
    desc_idx = [
        idx for idx, key in enumerate(documentation.keys()) if key == "description"
    ]
    # insert version_added after the description
    documentation.insert(desc_idx[0] + 1, key="version_added", value="1.0.0")
    repl = ruamel.yaml.dump(documentation, None, ruamel.yaml.RoundTripDumper)

    # remove version added from anywhere else in the docstring if preceded by 1+ spaces
    example_lines = repl.splitlines()
    regex = re.compile(r"^\s+version_added\:\s.*$")
    example_lines = [l for l in example_lines if not re.match(regex, l)]
    bodypart[0].value.value = "\n".join(example_lines)


def update_examples(bodypart, module_name, collection):
    """
    Update the example

    :param bodypart: The EXAMPLE section of the module
    :param module_name: The name of the module
    :param collection: The name of the collection
    """

    if len(bodypart) != 1:
        logging.warning("Failed to find EXAMPLES assignment")
        return
    full_module_name = "{collection}.{module_name}".format(
        collection=collection, module_name=module_name
    )
    example = ruamel.yaml.load(bodypart[0].value.value, ruamel.yaml.RoundTripLoader)
    # check each task and update to fqcn
    for idx, task in enumerate(example):
        example[idx] = ruamel.yaml.comments.CommentedMap(
            [
                (full_module_name, v) if k == module_name else (k, v)
                for k, v in task.items()
            ]
        )
    repl = ruamel.yaml.dump(example, None, ruamel.yaml.RoundTripDumper)

    # look in yaml comments for the module name as well and replace
    example_lines = repl.splitlines()
    for idx, line in enumerate(example_lines):
        if (
            line.startswith("#")
            and module_name in line
            and module_name
            and full_module_name not in line
        ):
            example_lines[idx] = line.replace(module_name, full_module_name)
    bodypart[0].value.value = "\n".join(example_lines)


def update_short_description(retrn, documentation, module_name):
    """
    Update the short description of the module

    :param bodypart: The DOCUMENTATION section of the module
    :param module_name: The module name
    """
    if len(retrn) != 1:
        logging.warning("Failed to find RETURN assignment")
        return
    ret_section = ruamel.yaml.load(retrn[0].value.value, ruamel.yaml.RoundTripLoader)
    if len(documentation) != 1:
        logging.warning("Failed to find DOCUMENTATION assignment")
        return
    doc_section = ruamel.yaml.load(
        documentation[0].value.value, ruamel.yaml.RoundTripLoader
    )
    short_description = doc_section['short_description']
    
    rm_rets = ["after", "before", "commands"]
    match = [x for x in rm_rets if x in list(ret_section.keys())]
    if len(match) == len(rm_rets):
        logging.info("Found a resource module")
        parts = module_name.split("_")
        # things like 'interfaces'
        resource = parts[1].lower()
        if resource in SPECIALS:
            resource = SPECIALS[resource]
        else:
            resource = resource.upper()
        if resource.lower()[-1].endswith("s"):
            chars = list(resource)
            chars[-1] = chars[-1].lower()
            resource = "".join(chars)
        if len(parts) > 2 and parts[2] != "global":
            resource += " {p1}".format(p1=parts[2])
        short_description = "{resource} resource module".format(resource=resource)
    # Check for deprecated modules
    if 'deprecated' in doc_section and not short_description.startswith('(deprecated)'):
        logging.info("Found to be deprecated")
        short_description = "(deprecated) {short_description}".format(short_description=short_description)
    # Change short if necessary
    if short_description != doc_section['short_description']:
        logging.info("Setting short desciption to '%s'", short_description)
        doc_section["short_description"] = short_description
        repl = ruamel.yaml.dump(doc_section, None, ruamel.yaml.RoundTripDumper)
        documentation[0].value.value = repl


def black(filename):
    """
    Run black against the file

    :param filename: The full path to the file
    """
    logging.info("Running black against %s", filename)
    subprocess.check_output(["black", "-q", filename])


def process(collection, path):
    """
    Process the files in each subdirectory
    """
    for subdir in SUBDIRS:
        dirpath = "{colpath}{collection}/plugins/{subdir}".format(
            colpath=path, collection=collection, subdir=subdir
        )
        for filename in os.listdir(dirpath):
            if filename.endswith(".py"):
                filename = "{dirpath}/{filename}".format(
                    dirpath=dirpath, filename=filename
                )
                logging.info("-------------------Processing %s", filename)
                ast_obj = load_py_as_ast(filename)

                # Get the module naem from the docstring
                module_name = retrieve_module_name(
                    find_assigment_in_ast(ast_file=ast_obj, name="DOCUMENTATION")
                )
                if not module_name:
                    logging.warning("Skipped %s: No module name found", filename)
                    continue

                # Update the metadata
                update_metdata(
                    bodypart=find_assigment_in_ast(
                        ast_file=ast_obj, name="ANSIBLE_METADATA"
                    )
                )
                logging.info("Updated metadata in %s", filename)

                # Update the documentation
                update_documentation(
                    bodypart=find_assigment_in_ast(
                        ast_file=ast_obj, name="DOCUMENTATION"
                    )
                )
                logging.info("Updated documentation in %s", filename)

                # Update the short description
                update_short_description(
                    retrn=find_assigment_in_ast(ast_file=ast_obj, name="RETURN"),
                    documentation=find_assigment_in_ast(
                        ast_file=ast_obj, name="DOCUMENTATION"
                    ),
                    module_name=module_name,
                )

                # Update the examples
                update_examples(
                    bodypart=find_assigment_in_ast(ast_file=ast_obj, name="EXAMPLES"),
                    module_name=module_name,
                    collection=collection,
                )
                logging.info("Updated examples in %s", filename)

                # Write out the file and black
                filec = LICENSE + astor.to_source(ast_obj)
                with open(filename, "w") as fileh:
                    fileh.write(filec)
                    logging.info("Wrote %s", filename)
                black(filename)


def main():
    """
    The entry point
    """
    if not platform.python_version().startswith("3.8"):
        sys.exit("Python 3.8+ required")
    parser = ArgumentParser()
    parser.add_argument(
        "-c", "--collection", help="The name of the collection", required=True
    )
    parser.add_argument(
        "-p", "--path", help="The path to the collection", required=True
    )
    args = parser.parse_args()
    process(collection=args.collection, path=args.path)


if __name__ == "__main__":
    main()