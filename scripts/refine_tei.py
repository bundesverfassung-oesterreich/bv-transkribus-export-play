import os
import glob
import shutil
import re
import lxml.etree as ET
import lxml.builder as builder
import jinja2
import csv
import json
from acdh_tei_pyutils.tei import TeiReader


TEI_DIR = "./editions"
TMP_DIR = "./mets/"
MALFORMED_FILES_LOGPATH = "./logs/malformed_files.csv"
doc_base_row_dump_url = "https://raw.githubusercontent.com/bundesverfassung-oesterreich/bv-entities/main/json_dumps/document.json"
project_base_row_dump_url = "https://raw.githubusercontent.com/bundesverfassung-oesterreich/bv-entities/main/json_dumps/project_data.json"
doc_local_baserow_dump = TMP_DIR + "document.json"
project_local_baserow_dump = TMP_DIR + "project_data.json"

# # setup metadata from json
PROJECT_MD = {}
with open(project_local_baserow_dump, "r", encoding="utf-8") as json_metadata_file:
    PROJECT_MD = json.load(json_metadata_file)["1"]

# # load template
templateLoader = jinja2.FileSystemLoader(searchpath="./scripts/templates")
templateEnv = jinja2.Environment(
    loader=templateLoader, trim_blocks=True, lstrip_blocks=True
)
template = templateEnv.get_template("tei_template.j2")
file_rename_errors = 0

# # xml factory

NewElement = builder.ElementMaker()


# # def funcs


class PersonMetaData:
    def __init__(self, name, role, arche_role):
        self.name = name
        self.role = role
        self.arche_role = arche_role

def get_xml_doc(xml_file):
    """
    returns TeiReader, if parsing creates error -> dict
    """
    try:
        return TeiReader(xml_file)
    except Exception as e:
        return {"file_name": xml_file, "error": e}


def log_nonvalid_files(malformed_xml_docs):
    if not malformed_xml_docs:
        if os.path.isfile(MALFORMED_FILES_LOGPATH):
            os.remove(MALFORMED_FILES_LOGPATH)
    else:
        fieldnames = ["file_name", "error"]
        log_directory, _ = os.path.split(MALFORMED_FILES_LOGPATH)
        if not os.path.exists(log_directory):
            os.makedirs(log_directory)
        with open(MALFORMED_FILES_LOGPATH, "w") as outfile:
            dict_writer = csv.DictWriter(outfile, fieldnames)
            dict_writer.writerows(malformed_xml_docs)


def seed_div_elements(doc: TeiReader, xpath_expr, regex_test, ana_val):
    reverse_ordered_div_elements = []
    section_head_strs = doc.any_xpath(xpath_expr)
    section_head_strs.reverse()
    for head_str in section_head_strs:
        head_str: ET._ElementUnicodeResult
        if re.match(regex_test, head_str.strip()):
            head_element = head_str.getparent()
            if head_str.is_tail:
                head_element.tag = "head"
                head_element.text = head_str
                head_element.tail = "\n"
                section_div = NewElement.div("\n", ana=ana_val)
                section_div.tail = "\n"
                head_element.addprevious(section_div)
                section_div.append(head_element)
                reverse_ordered_div_elements.append(section_div)
    return reverse_ordered_div_elements


def raise_div_element(section_div: ET._Element):
    parent_element = section_div.getparent()
    while parent_element.xpath("local-name()") != "div":
        if section_div.getprevious() is not None:
            parent_split_element = NewElement.stuff("\n")
            parent_split_element.tag = parent_element.tag
            siblings = section_div.xpath("following-sibling::*")
            for element in siblings:
                parent_split_element.append(element)
            parent_element.addnext(parent_split_element)
            parent_element.addnext(section_div)
        else:
            if parent_element.text and parent_element.text.strip():
                parent_split_element = NewElement.randomTagShouldntExist("\n")
                parent_split_element.tag = parent_element.tag
                parent_split_element.text = parent_element.text
                parent_element.text = ""
                parent_element.addprevious(parent_split_element)
            parent_element.addprevious(section_div)
        parent_element = section_div.getparent()


def expand_div_element(section_div: ET._Element, append_test):
    next_element = section_div.getnext()
    while append_test(next_element):
        section_div.append(next_element)
        next_element = section_div.getnext()


def make_all_section_divs(doc):
    article_ana = "article"
    # # make artikel-divs
    article_divs = seed_div_elements(
        doc,
        xpath_expr=r"//tei:body//tei:lb/following-sibling::text()[contains(., 'Art.')]",
        regex_test=r"^Art\.?( *$| .{0,10}$)",
        ana_val=article_ana,
    )
    # # move created divs to child-level of main div
    for div in article_divs:
        raise_div_element(div)

    # # place content in article divs
    for div in article_divs:
        expand_div_element(
            div,
            append_test=lambda next_element: bool(
                next_element is not None and next_element.xpath("local-name()!='div'")
            ),
        )


def remove_useless_atributes(doc: TeiReader):
    elements = doc.any_xpath(".//*[local-name()='lb' or local-name()='p' or local-name()='head']")
    for element in elements:
        element.attrib.clear()


def remove_useless_elements(doc: TeiReader):
    for parent in doc.any_xpath(
        "//tei:*[(local-name()='p' or local-name()='ab') and ./node()[1][normalize-space(.)=''] and ./*[1][local-name()='lb']]"
    ):
        parent.text = parent[0].tail
        parent.remove(parent[0])


def get_faksimile_element(doc: TeiReader, image_urls: list):
    for graphic_element in doc.any_xpath(".//tei:graphic"):
        graphic_element.attrib["url"] = image_urls.pop(0)
    for zone_element in doc.any_xpath(
        ".//tei:facsimile/tei:surface//tei:zone"
    ):
        zone_element.getparent().remove(zone_element)
    faksimile_element = doc.any_xpath(".//tei:facsimile")[0]
    return ET.tostring(faksimile_element).decode("utf-8")


def create_new_xml_data(
    doc: TeiReader,
    doc_metadata: dict,
    image_urls: list,
):
    # # get body & filename
    body_node = doc.any_xpath(".//tei:body")[0]
    make_all_section_divs(doc)
    remove_useless_atributes(doc)
    remove_useless_elements(doc)
    body = ET.tostring(body_node).decode("utf-8")
    body = body.replace('xmlns="http://www.tei-c.org/ns/1.0"', "")
    # # get faksimile
    faksimile = get_faksimile_element(doc, image_urls)
    # # get metadata
    context = {
        "project_md": PROJECT_MD,
        "doc_metadata": doc_metadata,
        "body": body,
        "faksimile": faksimile,
    }
    xml_data = template.render(context)
    doc = TeiReader(xml_data)
    doc.tree_to_file(os.path.join(TEI_DIR, doc_metadata["bv_id"]+".xml"))


def return_image_urls(mets_doc):
    """
    returns image links from mets file in doc order
    """
    return mets_doc.tree.xpath(
        "//ns3:fileGrp[@ID='IMG']/ns3:file/ns3:FLocat/@ns2:href",
        namespaces={
            "ns3": "http://www.loc.gov/METS/",
            "ns2": "http://www.w3.org/1999/xlink",
        },
    )


def return_transkribus_doc_id(xml_file_path):
    _, filename = os.path.split(xml_file_path)
    return filename.split("_")[0]


def return_mets_doc(transkribus_doc_id: str, transkribus_collection_id: str):
    mets_file_str = f"./mets/{transkribus_collection_id}/{transkribus_doc_id}_mets.xml"
    return TeiReader(mets_file_str)


def return_col_id_from_mets_doc(doc: TeiReader):
    try:
        return doc.tree.xpath(".//trpDocMetadata/docId/text()")[0]
    except IndexError:
        return ""


def fetch_metadata_dump(url, local_filepath):
    import requests

    results = requests.get(url)
    json_result = results.json()
    with open(local_filepath, "w") as outfile:
        json.dump(json_result, outfile)
    return json_result


def load_metadata_from_dump():
    json_data = None
    if not os.path.exists(doc_local_baserow_dump):
        _ = fetch_metadata_dump(
            url=project_base_row_dump_url, local_filepath=project_local_baserow_dump
        )
        json_data = fetch_metadata_dump(
            url=doc_base_row_dump_url, local_filepath=doc_local_baserow_dump
        )
    else:
        with open(doc_local_baserow_dump, "r") as infile:
            json_data = json.load(infile)
    meta_data_objs_by_transkribus_id = {}
    for row in json_data.values():
        mets_dict = row
        transcribus_col_id = mets_dict["transkribus_col_id"]
        if transcribus_col_id not in meta_data_objs_by_transkribus_id:
            meta_data_objs_by_transkribus_id[transcribus_col_id] = {}
        meta_data_objs_by_transkribus_id[transcribus_col_id][mets_dict["transkribus_doc_id"]] = mets_dict
    return meta_data_objs_by_transkribus_id


def process_all_files():
    # # load metadata
    metadata = load_metadata_from_dump()
    for transkribus_collection_id, collection_metadata in metadata.items():
        # # load sourcefiles from fetch / transform job
        source_files = glob.glob(f"{TMP_DIR}/{transkribus_collection_id}/*_tei.xml")
        malformed_xml_docs = []
        for xml_file_path in source_files:
            # # parsing doc to mem
            doc = get_xml_doc(xml_file_path)
            if isinstance(doc, dict):
                malformed_xml_docs.append(doc)
            else:
                # # important stuff happens here
                # # organize data yet missing in the final doc
                transkribus_doc_id = return_transkribus_doc_id(xml_file_path)
                doc_metadata = collection_metadata[transkribus_doc_id]
                mets_doc = return_mets_doc(
                    transkribus_doc_id, transkribus_collection_id
                )
                image_urls = return_image_urls(mets_doc)
                # # change the doc / write data to it
                create_new_xml_data(doc, doc_metadata, image_urls)
    return malformed_xml_docs


if __name__ == "__main__":
    # # clear directory for new export
    shutil.rmtree(TEI_DIR, ignore_errors=True)
    os.makedirs(TEI_DIR, exist_ok=True)
    # # load / process all unprocessed files
    malformed_xml_docs = process_all_files()
    log_nonvalid_files(malformed_xml_docs)
    if file_rename_errors != 0:
        print(
            f"\n{file_rename_errors} file(s) couldn???t be renamed since title wasn???t found in xml\n"
        )
    os.remove(doc_local_baserow_dump)
