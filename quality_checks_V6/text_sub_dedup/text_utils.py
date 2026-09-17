#!/usr/bin/env python3
# -*- coding:utf-8 -*-
########################################################################
#
# Copyright (c) 2023 Baidu.com, Inc. All Rights Reserved
#
########################################################################
"""
Author :   caiyuanjun
E-mail :   caiyuanjun@baidu.com
Date   :   23/04/10 08:49:32
Desc   :   文本处理相关工具
"""

import sys
import re
import os
import logging
import re
import unicodedata
import io

CUR_DIR = os.path.dirname(os.path.abspath(__file__))
log_format = '''[%(levelname)s] [%(asctime)s] [%(threadName)s] [%(name)s] '''
log_format += '''[%(filename)s:%(funcName)s:%(lineno)d]: %(message)s'''
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format=log_format
)

UNICODE_PUNCT = {
    "，": ",",
    "。": ".",
    "、": ",",
    "„": '"',
    "”": '"',
    "“": '"',
    "«": '"',
    "»": '"',
    "１": '"',
    "」": '"',
    "「": '"',
    "《": '"',
    "》": '"',
    "´": "'",
    "∶": ":",
    "：": ":",
    "？": "?",
    "！": "!",
    "（": "(",
    "）": ")",
    "；": ";",
    "–": "-",
    "—": " - ",
    "．": ". ",
    "～": "~",
    "’": "'",
    "…": "...",
    "━": "-",
    "〈": "<",
    "〉": ">",
    "【": "[",
    "】": "]",
    "％": "%",
    "►": "-",
}

SPLIT_PUNCT_RE = re.compile("[,.?!;。！？；，\[\]\(\):#=|\-\/]")

UNICODE_PUNCT_RE = re.compile(f"[{''.join(UNICODE_PUNCT.keys())}]")


def replace_unicode_punct(text: str) -> str:
    """ replace_unicode_punct """
    return "".join((UNICODE_PUNCT.get(c, c) for c in text))


def remove_unicode_punct(text: str) -> str:
    """More aggressive version of replace_unicode_punct but also faster."""
    return UNICODE_PUNCT_RE.sub("", text)


def remove_split_punct(text: str) -> str:
    """More aggressive version of replace_unicode_punct but also faster."""
    return SPLIT_PUNCT_RE.sub("", text)


def strip_accents(line: str) -> str:
    """Strips accents from a piece of text."""
    nfd = unicodedata.normalize("NFD", line)
    output = [c for c in nfd if unicodedata.category(c) != "Mn"]
    if len(output) == line:
        return line
    return "".join(output)


# Build a regex matching all control characters.
NON_PRINTING_CHARS_RE = re.compile(
    f"[{''.join(map(chr, list(range(0, 32)) + list(range(127, 160))))}]"
)
DIGIT_RE = re.compile(r"\d")
PUNCT_OR_NON_PRINTING_CHARS_RE = re.compile(
    (UNICODE_PUNCT_RE.pattern + NON_PRINTING_CHARS_RE.pattern).replace("][", "")
)


def remove_non_printing_char(text: str) -> str:
    """ remove_non_printing_char """
    return NON_PRINTING_CHARS_RE.sub("", text)


def normalize_spacing_for_tok(text: str, language: str = "en") -> str:
    """ normalize_spacing_for_tok """
    res = (
        text.replace("\r", "")
        # remove extra spaces
        .replace("(", " (")
        .replace(")", ") ")
        .replace(" +", " ")
    )
    res = re.sub(r"\) ([\.\!\:\?\;\,])", r"\)\1", res)
    res = res.replace("( ", "(").replace(" )", ")")
    res = re.sub(r"(\d) \%", r"\1\%", res)
    res = res.replace(" :", ":").replace(" ;", ";")
    res = res.replace("`", "'").replace("''", ' " ')

    res = (
        res.replace("„", '"')
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", " - ")
        .replace(" +", " ")
        .replace("´", "'")
        .replace("([a-z])‘([a-z])", r"\1'\2/")
        .replace("([a-z])’([a-z])", r"\1'\2/")
        .replace("‘", '"')
        .replace("‚", '"')
        .replace("’", '"')
        .replace("''", '"')
        .replace("´´", '"')
        .replace("…", "...")
        # French quotes
        .replace(" « ", ' "')
        .replace("« ", '"')
        .replace("«", '"')
        .replace(" » ", '" ')
        .replace(" »", '"')
        .replace("»", '"')
        # handle pseudo-spaces
        .replace(" %", "%")
        .replace("nº ", "nº ")
        .replace(" :", ":")
        .replace(" ºC", " ºC")
        .replace(" cm", " cm")
        .replace(" ?", "?")
        .replace(" !", "!")
        .replace(" ;", ";")
        .replace(", ", ", ")
        .replace(" +", " ")
        .replace("．", ". ")
    )
    # English "quotation," followed by comma, style
    if language == "en":
        res = re.sub(r"\"([,\.]+)", r"\1\"", res)
    # Czech is confused
    elif language == "cs" or language == "cz":
        pass
    # German/Spanish/French "quotation", followed by comma, style
    else:
        res = res.replace(',"', '",')
        res = re.sub(
            r"(\.+)\"(\s*[^<])", r"\"\1\2", res
        )  # don't fix period at end of sentence

    if (
            language == "de"
            or language == "es"
            or language == "cz"
            or language == "cs"
            or language == "fr"
    ):
        res = re.sub(r"(\d) (\d)", r"\1,\2", res)
    else:
        res = re.sub(r"(\d) (\d)", r"\1.\2", res)
    return res


def normalize(line: str, accent=True, case=True, numbers=True, punct=1) -> str:
    """ normalize """
    line = line.strip()
    if not line:
        return line
    if case:
        line = line.lower()
    if accent:
        line = strip_accents(line)
    if numbers:
        line = DIGIT_RE.sub("0", line)
    if punct == 1:
        line = replace_unicode_punct(line)
    elif punct == 2:
        line = remove_unicode_punct(line)
    line = remove_non_printing_char(line)
    return line


def slow_normalize_for_dedup(line: str) -> str:
    """ slow_normalize_for_dedup """
    return normalize(line, accent=False, case=True, numbers=True, punct=2)


def normalize_for_dedup(line: str) -> str:
    """ normalize_for_dedup """
    line = line.strip()
    if not line:
        return line
    # case
    line = line.lower()
    # numbers
    line = DIGIT_RE.sub("0", line)
    line = PUNCT_OR_NON_PRINTING_CHARS_RE.sub("", line)
    line = re.sub('\s+', ' ', line)
    return line


def remove_punctuation(sentence):
    """ 使用正则表达式匹配句子末尾的标点符号，并替换为空字符串 """
    cleaned_sentence = re.sub(r'[^\w\s]+$', '', sentence)
    return cleaned_sentence.strip()  # 去除首尾空格


def split_mix_paragraph(text: str, keep_delimiter: bool = True):
    """将多语言段落切分成句子
    """
    text = re.sub("\1", "", text)
    # 定义正则表达式，用于匹配句子分割符号
    sentence_delimiters = re.compile(r'(?<=[,.?!;。！？；，])\s*')
    # 定义正则表达式，用于匹配英文缩写
    # 例如 Mr. 或者 Mrs.
    alphabets = u"([A-Za-z])"
    prefixes = u"(Mr|St|Mrs|Ms|Dr|Prof|Capt|Cpt|Lt|Mt)\. ([A-Za-z])"

    # 定义正则表达式，用于匹配带小数点的数字
    # 例如 4.12
    nums = r'(\d+)\.(\d+)'

    # 将所有的缩写放在一起
    # 例如 "Mr. Smith was here." -> "Mr\1Smith was here."
    def repl(m):
        return m.group(1) + "\1" + m.group(2)

    text = re.sub(prefixes, repl, text)

    def repl2(m):
        return m.group(1) + "\2" + m.group(2)

    text = re.sub(nums, repl2, text)

    # 切分句子
    sentences = sentence_delimiters.split(text)

    for i in range(len(sentences)):
        # 还原英文缩写
        # 例如 "Mr\1Smith was here." -> "Mr. Smith was here."
        sentences[i] = re.sub(u"(\1)", ". ", sentences[i])
        sentences[i] = re.sub(r"(\s+)", " ", sentences[i])
        # 还原带小数点的数字
        sentences[i] = re.sub(u"(\2)", ".", sentences[i])

    sentences = [x for x in sentences if x != '']
    if len(sentences) == 0:
        return []

    # 把只有一个分隔符的句子加到前一个句子上
    new_sentences = []
    cur_sent = sentences[0]
    for index in range(1, len(sentences)):
        if len(sentences[index]) == 1:
            cur_sent += sentences[index]
        else:
            if keep_delimiter:
                new_sentences.append(cur_sent)
            else:
                new_sentences.append(remove_punctuation(cur_sent))
            cur_sent = sentences[index]
    if keep_delimiter:
        new_sentences.append(cur_sent)
    else:
        new_sentences.append(remove_punctuation(cur_sent))

    new_sentences = [x for x in new_sentences if x != '']

    return new_sentences


def remove_unprintable(in_strs):
    """remove_unprintable
    """
    return ''.join(x for x in in_strs if x.isprintable())


def in_stream(encoding="utf-8", errors="replace"):
    """
    获取输入流
    """
    for line in io.open(0, 'r', encoding=encoding, errors=errors, newline="\n"):
        line = line.replace('\r', '').strip('\n')
        yield line


def get_offset_map(org_text, text):
    """
    org_text: original text
    text: text after remove punctuation and whitespace
    compute the offset map from new_offset to old_offset
    """
    offset_map = dict()
    new_index = 0
    # print(len(text))
    for index, char in enumerate(org_text):
        if new_index >= len(text):
            break
        if char == text[new_index]:
            offset_map[new_index] = index
            # print(text[new_index], char)
            new_index += 1
    offset_map[len(text)] = len(org_text)
    return offset_map

def remove_punctuation_and_whitespace(text):
    """

    :param text:
    :return:
    """
    # 去除中文标点符号
    chinese_punctuation_pattern = re.compile("[\u3000-\u303F\uFF00-\uFFEF]")
    text = chinese_punctuation_pattern.sub("", text)

    # 去除英文标点符号
    english_punctuation_pattern = re.compile("[!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~]")
    text = english_punctuation_pattern.sub("", text)

    # 去除换行符和空格
    text = re.sub(r'\s+', '', text)

    return text


def has_cn_char(text):
    """
    :param text:
    :return:
    """
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            return True
    return False

def has_en_char(text):
    """
    :param text:
    :return:
    """
    for char in text:
        if "a" <= char <= "z" or "A" <= char <= "Z":
            return True
    return False

def has_target_char(text, lang):
    """
    :param text:
    :param lang:
    :return:
    """
    if lang == "en":
        return has_en_char(text)
    elif lang == "ch":
        return has_cn_char(text)
    else:
        return has_cn_char(text) or has_en_char(text)

def is_code(data):
    """

    :param data:
    :return:
    """
    if "code::" in data:
        return True

    pattern = r"```([\s\S]*?)```"
    # use search (not findall) for efficiency
    match = re.search(pattern, data)
    if match:
        return True
    return False

