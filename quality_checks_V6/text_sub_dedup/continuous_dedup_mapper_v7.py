#!/usr/bin/env python3
########################################################################
#
# Copyright (c) 2023 Baidu.com, Inc. All Rights Reserved
#
########################################################################
"""
Author :   zhaojun16
E-mail :   zhaojun16@baidu.com
Date   :   23/01/03 08:49:32
Desc   :   连续重复
"""
import re
import sys
import json
import time
import traceback
from text_utils import in_stream, get_offset_map, remove_punctuation_and_whitespace
from text_utils import has_target_char, is_code

EN_FILE_STR = ["Ha", "HA", "ha"]

# 分别表示长度的范围的最小值门限、最大值门限、切分长度、重叠的字符数
VALUE_RANGE = [
    (0, 10000, (10000, 0)),
    (10000, 300000, (10000, 1000)),
    (300000, 500000, (5000, 200)),
    (500000, 800000, (3000, 100)),
    (800000, 100000000000000, (1000, 10)),
]

# LENGTH_RANGE = {
#     (1, 2): (18, 30),
#     (2, 3): (15, 20),
#     (3, 4): (12, 16),
#     (4, 5): (10, 13),
#     # 添加其他区间和相应的值
# }

# LENGTH_RANGE_OTHER = {
#     (0, 1): (13, 25),
#     (1, 2): (18, 30),
#     (2, 3): (15, 20),
#     (3, 4): (12, 16),
#     (4, 5): (10, 13),
#     (5, 10): (10, 18),
#     (10, 20): (8, 18),
#     (20, 30): (6, 15),
#     (30, 100000): (3, 15),
#     # 添加其他区间和相应的值
# }

MIN_CONTINUOUS_REPEAT_NUM = 5


def extract_repeated_phrases(text, min_short_length, min_long_length):
    """
    :param text:
    :param min_length:
    :param middle_length:
    :return:
    """
    # This regular expression looks for any repeated substring in the text
    max_short_length = max(min(len(text) // 3, min_long_length), min_short_length)
    max_long_length = max(len(text) // 2, min_long_length)
    pattern_short = re.compile(r'(.{%d,%d}?)\1\1' % (min_short_length, max_short_length), re.DOTALL)
    pattern_long = re.compile(r'(.{%d,%d}?)\1' % (min_long_length, max_long_length), re.DOTALL)

    continuous_res_short = []
    for match in pattern_short.finditer(text):
        repeat_text_len = len(match.group(1))
        repeat_text_start = match.span()[0]
        continuous_res_short.append((repeat_text_start, repeat_text_start + repeat_text_len))
    continuous_res_long = []
    if len(text) >= min_long_length:
        for match in pattern_long.finditer(text):
            repeat_text_len = len(match.group(1))
            repeat_text_start = match.span()[0]
            continuous_res_long.append((repeat_text_start, repeat_text_start + repeat_text_len))
    return continuous_res_short, continuous_res_long


def get_repeat_num_and_end(content, offset, repeat_text):
    """
    :param content:
    :param offset:
    :param repeat_text
    :return: the number of occurrences of repeat_text in content[offset:] and the actual end offset
    """
    k = 0
    repeat_text_len = len(repeat_text)
    stripped_repeat_text = repeat_text.rstrip()
    trailing_space_len = repeat_text_len - len(stripped_repeat_text)
    while True:
        if content[offset:offset + repeat_text_len] == repeat_text:
            k += 1
            offset += repeat_text_len
        elif trailing_space_len and content[offset:offset + len(stripped_repeat_text)] == stripped_repeat_text:
            next_offset = offset + len(stripped_repeat_text)
            if next_offset == len(content) or content[next_offset:next_offset + trailing_space_len].isspace():
                k += 1
                offset = next_offset
            break
        else:
            break
    return k, offset


def init_bitmap(length):
    """init bitmap"""
    return [False] * length


def set_bitmap(bitmap, start, end, val):
    """set bitmap"""
    for i in range(start, end):
        bitmap[i] = val


def check_bitmap_strict(bitmap, start, end):
    """
    return True only if every element in bitmap[start:end] is True
    """
    for i in range(start, end):
        if not bitmap[i]:
            return False
    return True


def find_repeated_phrases(text, min_short_length, min_long_length):
    """

    :param text:
    :param min_short_length:
    :param min_long_length
    :return:
    """
    # This regular expression looks for any repeated substring in the text
    result = []
    case_bitmap = init_bitmap(len(text))
    short_repeats, long_repeats = extract_repeated_phrases(text, min_short_length, min_long_length)
    for start, end in short_repeats:
        # if len(result) > 0 and start < result[-1][2]:
        #     continue
        repeat_text = text[start:end]
        repeat_num, repeat_end = get_repeat_num_and_end(text, start, repeat_text)
        if check_bitmap_strict(case_bitmap, start, repeat_end):
            continue
        result.append((start, end, repeat_end, repeat_num))
        set_bitmap(case_bitmap, start, repeat_end, True)
        
    # print("short:", json.dumps(result, ensure_ascii=False))

    short_size = len(result)
    for start, end in long_repeats:
        # if start >= short_size and end <= result[-1][2]:
        #     continue
        repeat_text = text[start:end]
        repeat_num, repeat_end = get_repeat_num_and_end(text, start, repeat_text)
        if check_bitmap_strict(case_bitmap, start, repeat_end):
            continue
        result.append((start, end, repeat_end, repeat_num))
        set_bitmap(case_bitmap, start, repeat_end, True)
        
    # print("all:", json.dumps(result, ensure_ascii=False))
    return result


def check_cn_standard(target_len, repeat_num):
    """
    :param target_len:
    :param repeat_num:
    :return:
    """
    return target_len > 0 and repeat_num >= MIN_CONTINUOUS_REPEAT_NUM


def check_en_standard_char(target_len, repeat_num):
    """
    :param target_len:
    :param repeat_num:
    :return:
    """
    return False


def check_en_standard_word(target_len, repeat_num):
    """
    :param target_len:
    :param repeat_num:
    :return:
    """
    return target_len > 0 and repeat_num >= MIN_CONTINUOUS_REPEAT_NUM


def keep_chinese_characters(text):
    """

    :param text:
    :return:
    """
    cleaned_text = re.sub(r'[^\u4e00-\u9fa5]', '', text)
    return cleaned_text


def keep_english_characters(text):
    """
    :param text:
    :return:
    """
    cleaned_text = re.sub(r'[^a-zA-Z]', '', text)
    return cleaned_text


def is_paragraph_start(text, offset):
    """
    判断给定的offset是否是一个段落的起始位置（前面有空格也算）
    
    参数:
    text (str): 输入的文本
    offset (int): 需要判断的位置偏移量
    
    返回:
    bool: 如果offset是段落起始位置返回True，否则返回False
    """
    # 处理边界情况
    if offset < 0 or offset > len(text):
        return False
    
    # 偏移量为0时，认为是段落起始
    if offset == 0:
        return True
    
    # 获取offset前一个字符
    prev_char = text[offset - 1]
    
    # 如果前一个字符是换行符或连续的空格，则认为是段落起始
    if prev_char == '\n':
        return True
    
    # 检查前面是否有连续的空格
    space_count = 0
    pos = offset - 1
    while pos >= 0 and text[pos].isspace() and text[pos] != '\n':
        space_count += 1
        pos -= 1
    
    # 如果前面有连续的空格，且空格前是换行符或文本开头，则认为是段落起始
    if space_count > 0 and (pos < 0 or text[pos] == '\n'):
        return True
    
    return False


def get_next_nonspace_offset(text, offset):
    """get the next non-space offset for the given offset"""
    if offset < 0 or offset >= len(text):
        return offset
    while offset < len(text) and text[offset].isspace():
        offset += 1

    return offset


def detect_continuous_repetition(data):
    """
    连续重复 5 次及以上才判为异常重复。
    :param data:
    :return:
    """
    # repeat_info = dict()
    continuous_repeat_data = list()
    # 代码的检测
    if is_code(data):
        return continuous_repeat_data

    org_data = data
    data = remove_punctuation_and_whitespace(org_data)
    # move fast detection here
    if not need_detect_continuous_char(
        data, short_sizes=[2, 10], long_sizes=[250], single_repeat_num=MIN_CONTINUOUS_REPEAT_NUM, lang="ch"
    ):
        return continuous_repeat_data

    repeat_res = find_repeated_phrases(data, min_short_length=1, min_long_length=250)
    if not repeat_res:
        return continuous_repeat_data

    offset_map = get_offset_map(org_data, data)

    # exist_repeat_one = False
    # exist_repeat_two = False
    # exist_repeat_three = False
    # done_set = set()
    # one_dedup_set = set()
    # two_dedup_set = set()
    for start, end, repeat_end, repeat_num in repeat_res:
        repeat_text = data[start:end]
        mapped_start = offset_map[start]
        mapped_end = offset_map[end]
        mapped_repeat_end = offset_map[repeat_end]
        org_repeat_text = org_data[mapped_start:mapped_end]

        # default: keep the first occurrence and mask the rest
        # mapped_mask_start = offset_map[end]
        mapped_mask_start = offset_map[end - 1] + 1
        # mapped_mask_end = mapped_repeat_end
        mapped_mask_end = offset_map[repeat_end - 1] + 1

        # if repeat_text in done_set:
        #     continue

        if repeat_text in ['\\\\u0000', '\\\\U0000'] and repeat_num >= MIN_CONTINUOUS_REPEAT_NUM:
            continuous_repeat_data.append(
                (mapped_start, mapped_end, mapped_mask_start, mapped_mask_end, repeat_num, len(repeat_text), 0, True))
            # exist_repeat_one = True
            continue
        # 这个是去掉标点符号的
        # repeat_text_t = re.sub(r'[^\x00-\x7F]+', '', repeat_text)
        # chinese_characters = re.search(r'[\u4e00-\u9fff]+', repeat_text)
        # chinese_characters = any('\u4e00' <= ch <= '\u9fff' for ch in repeat_text)
        if has_target_char(repeat_text, "ch"):
            # 单个中文字需要重复3个及以上吧
            keep_characters = keep_chinese_characters(repeat_text)
            keep_characters_len = len(keep_characters)
            # is_dup, dup_num = get_dedup_num(repeat_text, keep_characters, data)
            is_dup = check_cn_standard(keep_characters_len, repeat_num)
            if not is_dup:
                continue

            # if "====" in org_data or "---+" in org_data or "----|" in org_data:
            if "|" in org_repeat_text:
                continue
            if (org_repeat_text.startswith("\n*") or org_repeat_text.startswith("*\n")) and \
                org_repeat_text.count('\n') == 1:
                continue
            # if is_dup and dup_num <= 4 and 2 <= len(repeat_text) <=8 and (
            #         repeat_text[0] == '\n' or repeat_text[1] == '\n') and repeat_text.count('\n') == 1:
            #     continue
            # if is_dup and dup_num == 3 and 9 <= len(repeat_text) and (
            #         repeat_text[0] == '\n' or repeat_text[1] == '\n') and repeat_text.count('\n') == 1:
            #     continue
            # 特殊的case

            if "相关知识点：" in org_data and "解析" in org_data and repeat_num == 3:
                pattern = r"相关知识点：([\s\S]*?)解析"
                matches = re.search(pattern, org_data)
                if matches and org_repeat_text in matches.group(1):
                    continue
            # if is_dup and dup_num >= 6 and len(keep_characters) == 1:
            #     one_dedup_set.add(repeat_text)
            # if is_dup and dup_num >= 6 and len(keep_characters) > 1:
            #     two_dedup_set.add(repeat_text)

            if repeat_num == 3 and keep_characters_len <= 10 and data.startswith(repeat_text):
                continue

            
            last_repeat_start = repeat_end - (end - start)
            mapped_last_repeat_start = offset_map[last_repeat_start]
            first_newpara_flag = is_paragraph_start(org_data, mapped_start)
            last_newpara_flag = is_paragraph_start(org_data, mapped_last_repeat_start)
            last_inpara_flag = (not is_paragraph_start(org_data, mapped_repeat_end)) and \
                mapped_repeat_end != len(org_data)
            if last_newpara_flag and last_inpara_flag:
                if repeat_num == 3 and first_newpara_flag:
                    continue
            if first_newpara_flag and last_inpara_flag: 
                mapped_mask_start = mapped_start
                mapped_mask_end = mapped_last_repeat_start

            # if repeat_text in ["哈", "啦"]:
            #     if repeat_num <= 6:
            #         continue
            #     else:
            #         mapped_mask_start = offset_map[start + 6]
            #         mapped_mask_end = mapped_repeat_end


            continuous_repeat_data.append(
                (mapped_start, mapped_end, mapped_mask_start, mapped_mask_end, 
                repeat_num, keep_characters_len, 0, False))


    # if len(one_dedup_set) >= 6:
    #     continuous_repet_data.append(
    #         {"content": "_".join(list(one_dedup_set)), "repeat_num": 6})
    #     exist_repeat_one = True
    # #
    # if len(two_dedup_set) >= 6:
    #     continuous_repet_data.append(
    #         {"content": "_".join(list(two_dedup_set)), "repeat_num": 6})
    #     exist_repeat_two = True

    # if any([exist_repeat_one, exist_repeat_two]):
    #     repeat_info = {"repeat_info": continuous_repet_data, "exist_repeat_one": exist_repeat_one,
    #                    "exist_repeat_two": exist_repeat_two}
    return continuous_repeat_data


def filter_latex_str(text):
    """

    :param text:
    :return:
    """
    # 定义正则表达式模式
    pattern = r"\\cdots|\\underline"

    # 使用re.sub函数替换匹配的部分为空字符串
    result = re.sub(pattern, "", text)
    return result


def detect_continuous_repetition_en(data):
    """
    连续重复 5 次及以上才判为异常重复。
    :param data:
    :return:
    """
    # repeat_info = dict()
    continuous_repeat_data = list()

    # 代码的检测
    # t0 = time.time()
    if is_code(data):
        return continuous_repeat_data

    if " song " in data or "♫" in data or "♪" in data:
        return continuous_repeat_data

    if "---------------" in data:
        return continuous_repeat_data
    # print("code time", time.time() - t0)
    # content = data.replace(" ", "")




    if not need_detect_continuous_word(
        data, short_sizes=[], long_sizes=[2,], single_repeat_num=MIN_CONTINUOUS_REPEAT_NUM, lang="en"
    ):
    # if not need_detect_continuous_en(org_data):
    # if not need_detect_continuous_char(data, short_sizes=[], long_sizes=[2,], single_repeat_num=4, lang="en"):
        # print("fast")
        return continuous_repeat_data

    org_data = data
    # data = remove_punctuation_and_whitespace(org_data)


    # t0 = time.time()
    repeat_res = find_repeated_phrases(data, min_short_length=1, min_long_length=4)
    if not repeat_res:
        return continuous_repeat_data

    offset_map = get_offset_map(org_data, data)
    # t1 = time.time()
    # print("*******", t1 - t0)

    # exist_repeat_one = False
    # exist_repeat_two = False
    # exist_repeat_three = False
    # done_set = set()
    for start, end, repeat_end, repeat_num in repeat_res:
        repeat_text = data[start:end]
        mapped_start = offset_map[start]
        mapped_end = offset_map[end]
        mapped_repeat_end = offset_map[repeat_end]
        org_repeat_text = org_data[mapped_start: mapped_end]
        # if repeat_text in done_set:
        #     continue
        # else:
        #     done_set.add(repeat_text)
        mapped_mask_start = offset_map[end]
        mapped_mask_end = mapped_repeat_end
        
        if repeat_text in ['\\\\u0000', '\\\\U0000'] and repeat_num >= MIN_CONTINUOUS_REPEAT_NUM:
            continuous_repeat_data.append(
                (mapped_start, mapped_end, mapped_mask_start, mapped_mask_end, repeat_num, len(repeat_text), 0, True))
            # exist_repeat_one = True
            continue
        # en_characters = re.search(r'[a-zA-Z]', repeat_text)
        # chinese_characters = any('\u4e00' <= ch <= '\u9fff' for ch in repeat_text)
        # “%d”，"%s"出现时，是代码的概率比较大，需要过滤掉
        pattern = r"%[ds]"
        if re.search(pattern, org_repeat_text):
            continue
        if repeat_text in EN_FILE_STR:
            continue
        # 必须是英文且有空格
        if has_target_char(repeat_text, "en"):
            repeat_text_split_list = org_repeat_text.split(" ")
            repeat_text_list = list()
            for repeat_str_item in repeat_text_split_list:
                if len(repeat_str_item) > 0 and (re.search(r'[a-zA-Z\d]', filter_latex_str(repeat_str_item)) or (
                        len(repeat_text_split_list) >= 5 or repeat_str_item in ["©", "", "*", '\n-', "...", "."])):
                    repeat_text_list.append(repeat_str_item)
            keep_characters = keep_english_characters(repeat_text)
            char_len = len(keep_characters)
            word_len = 0 if re.search(r'\s', org_repeat_text) is None else len(repeat_text_list)
            is_dup = check_en_standard_char(char_len, repeat_num) or check_en_standard_word(word_len, repeat_num)
            if not is_dup:
                continue

            # repeat_text_2 = "".join(repeat_text_list)
            # # 单个中文字需要重复3个及以上吧
            # is_dup = False
            # dup_num = 0
            # start_index = 6 if len(repeat_text_list) == 1 else 3
            # for num in range(start_index, 10):
            #     if repeat_text_2 * num not in content or (
            #             len(repeat_text_list) == 1 and repeat_text_2 not in data):
            #         break
            #     else:
            #         is_dup = True
            #         dup_num = num
            if "|" in org_repeat_text:
                continue

            if "</" in org_repeat_text:
                continue

            if "{" in org_repeat_text and "}" in org_repeat_text:
                continue

            # if " song " in org_data or "♫" in org_data or "♪" in org_data:
            #     continue
            
            last_repeat_start = repeat_end - (end - start)
            mapped_last_repeat_start = offset_map[last_repeat_start]
            
            mapped_last_repeat_start_next_ns = get_next_nonspace_offset(org_data, mapped_last_repeat_start)
            mapped_repeat_end_next_ns = get_next_nonspace_offset(org_data, mapped_repeat_end)
            mapped_start_next_ns = get_next_nonspace_offset(org_data, mapped_start)

            first_newpara_flag = is_paragraph_start(org_data, mapped_start_next_ns)
            last_newpara_flag = is_paragraph_start(org_data, mapped_last_repeat_start_next_ns)
            last_inpara_flag = (not is_paragraph_start(org_data, mapped_repeat_end_next_ns)) and \
                mapped_repeat_end_next_ns != len(org_data)



            # print(is_paragraph_start(org_data, mapped_last_repeat_start_next_ns))
            # print(is_paragraph_start(org_data, mapped_repeat_end_next_ns))
            if last_newpara_flag and last_inpara_flag:
                if repeat_num == 3 and first_newpara_flag:
                    continue
            if first_newpara_flag and last_inpara_flag: 
                mapped_mask_start = mapped_start
                mapped_mask_end = mapped_last_repeat_start

                

            continuous_repeat_data.append(
                (mapped_start, mapped_end, mapped_mask_start, mapped_mask_end, repeat_num, char_len, word_len, False))

    # if any([exist_repeat_one, exist_repeat_two]):
    #     repeat_info = {"repeat_info": continuous_repet_data, "exist_repeat_one": exist_repeat_one,
    #                    "exist_repeat_two": exist_repeat_two}
    return continuous_repeat_data


def split_long_str(long_str, size=10000, overlap=1000):
    """
    将长字符串切分为长度为size的子字符串
    :param long_str:
    :param size: 每个片段的大小
    :param overlap: 重叠的字符数
    :return:
    """
    chunks = [long_str[i:i + size] for i in range(0, len(long_str), size - overlap)]
    # # 2024.04.09 add chunk_offsets
    chunk_offsets = [i for i in range(0, len(long_str), size - overlap)]
    return chunks, chunk_offsets


def get_value_from_ranges(value, value_ranges):
    """
    Value_ranges = [
    (0, 10000, (10000,0)),
    (10000, 500000, (10000,1000)),
    (500000, 500000, (10000,1000)),
    (500000, 1000000, (5000,0)),
    (1000000, 5000000, (1000,0)),
    ]
    # 分别表示长度的范围的最小值门限、最大值门限、切分长度、重叠的字符数
    :param value:
    :param value_ranges:
    :return:
    """
    for range_start, range_end, range_value in value_ranges:
        if range_start <= value < range_end:
            return range_value
    return 1000, 10


def get_new_pos(offset, src_len, is_start):
    """map the offset to the new position in src or tgt"""
    if offset < src_len:
        new_offset = offset
        offset_from_src = True
    elif offset == src_len and is_start:
        new_offset = 0
        offset_from_src = False
    elif offset == src_len and not is_start:
        new_offset = src_len - 1
        offset_from_src = True
    else:
        new_offset = offset - src_len - 1
        offset_from_src = False
    return new_offset, offset_from_src


def convert_start_and_end(is_content, src_len, tgt_len, start, end):
    """convert start and end to new position"""
    if is_content:
        return [([start, end], "content")]
    elif src_len == 0 and tgt_len == 0:
        return [([0, 0], "src")]
    else:
        new_start, start_from_src = get_new_pos(start, src_len, True)
        new_end_incl, end_from_src = get_new_pos(end - 1, src_len, False)

        if start_from_src and end_from_src:
            return [([new_start, new_end_incl + 1], "src")]
        elif start_from_src and not end_from_src:
            return [([new_start, src_len], "src"), ([0, new_end_incl + 1], "tgt")]
        else:
            return [([new_start, new_end_incl + 1], "tgt")]


def detect_split_content(data, language):
    """

    :param data:
    :param language:
    :return:
    """
    # # 2024.04.09 move the need_detect_continuous to detect_continuous_repetition
    # # as current need_detect_continuous is not consistent with formal detection
    # # if no movement, to keep consistent, some operations like removing punct need to be repeated
    # continuous_res = need_detect_continuous(data, language, size=5)
    # if not continuous_res:
    #     return dict()
    # ta = time.time()
    size, overlap = get_value_from_ranges(len(data), VALUE_RANGE)
    # # 2024.04.09 add offset
    data_list, data_offsets = split_long_str(data, size=size, overlap=overlap)

    result = []
    for data_item, data_offset in zip(data_list, data_offsets):
        t0 = time.time()
        try:
            if language == "en":
                detect_res = detect_continuous_repetition_en(data_item)
            else:
                # content = data_item.strip().replace(" ", "")
                detect_res = detect_continuous_repetition(data_item)
            # print("detect time: {}".format(time.time() - t0))
        except Exception as _:
            sys.stderr.write(traceback.format_exc())
            detect_res = []

        # convert offset in detect_res
        for record in detect_res:
            mapped_start, mapped_end, mapped_mask_start, mapped_mask_end, \
                repeat_num, char_len, word_len, is_special = record
            mapped_start = data_offset + mapped_start
            mapped_end = data_offset + mapped_end
            mapped_mask_start = data_offset + mapped_mask_start
            mapped_mask_end = data_offset + mapped_mask_end
            # result.append((mapped_start, mapped_end, mapped_repeat_end, repeat_num, char_len, word_len, is_special))
            # new_start_end_list = convert_start_and_end(is_content, src_len, tgt_len, mapped_start, mapped_repeat_end)
            # for (new_start, new_end), source in new_start_end_list:
            result.append({
                "offset": [mapped_mask_start, mapped_mask_end],
                "score": repeat_num,
                "info": {
                    "repeat_text_offset": [mapped_start, mapped_end],
                    "char_len": char_len,
                    "word_len": word_len,
                    "is_special": int(is_special),
                },
                "model_type": "sub_dedup_continuous",
            })

        # result.extend(detect_res)
        t1 = time.time()
        # # need to process all the data, so not break
        # if detect_res:
        #     return detect_res
    return result


def need_detect_continuous_char(data, short_sizes, long_sizes, single_repeat_num, lang):
    """
    fast detect if repeat exists
    """
    # data = content.replace(" ", "")
    data_map = dict()
    # continuous_res = set()
    for data_index in range(0, len(data)):

        # 单个字的情况
        # 中文单个字的情况
        # simplify
        # if (data_index + 6 <= len(data) and len(set([char for char in data[data_index:data_index + 6]])) == 1
        #         and re.findall(r'[\u4e00-\u9fff]+', data[data_index])):
            # continuous_res.add(data[data_index])
        if data[data_index:data_index + single_repeat_num] == data[data_index] * single_repeat_num and \
            has_target_char(data[data_index], lang):
            return True

        # 连续片段
        # use short_sizes instead of one short_size to decrease false negatives: like axbaxcaxbaxcaxbaxc
        for k, size in enumerate(short_sizes + long_sizes):
            if data_index + size > len(data):
                continue
            slice_str = data[data_index:data_index + size]
            if slice_str not in data_map:
                data_map[slice_str] = [data_index]
            else:
                data_map[slice_str].append(data_index)

            # fix overlap case by data_map[slice_str][-1] - data_map[slice_str][-2] >= size
            if k < len(short_sizes):
                if (len(data_map[slice_str]) >= 3 and data_map[slice_str][-1] - data_map[slice_str][-2] >= size
                        and len(data) >= data_map[slice_str][-1] + data_map[slice_str][-1] - data_map[slice_str][-2]):
                    data_index_list = data_map[slice_str][-3:]
                    first_str = data[data_index_list[0]:data_index_list[1]]
                    second_str = data[data_index_list[1]:data_index_list[2]]
                    third_str = data[data_index_list[2]:(data_index_list[2] + data_index_list[2] - data_index_list[1])]
                    if first_str == second_str and second_str == third_str and has_target_char(first_str, lang):
                        # continuous_res.add(first_str)
                        return True
            else:
                if (len(data_map[slice_str]) >= 2 and data_map[slice_str][-1] - data_map[slice_str][-2] >= size
                        and len(data) >= data_map[slice_str][-1] + data_map[slice_str][-1] - data_map[slice_str][-2]):
                    data_index_list = data_map[slice_str][-2:]
                    first_str = data[data_index_list[0]:data_index_list[1]]
                    second_str = data[data_index_list[1]:(data_index_list[1] + data_index_list[1] - data_index_list[0])]
                    if first_str == second_str and has_target_char(first_str, lang):
                        return True

    # return continuous_res
    return False


def need_detect_continuous_word(data, short_sizes, long_sizes, single_repeat_num, lang):
    """
    fast detect if repeat exists
    """
    data = data.split()
    data_map = dict()
    # continuous_res = set()
    for data_index in range(0, len(data)):
        #  英文单个word的情况
        # if (data_index + 6 <= len(data) and len(set(data[data_index:data_index + 6])) == 1
        #         and re.search(r'[a-zA-Z]', ''.join(data[data_index]))):
        #     continuous_res.add(data[data_index])

        if "".join(data[data_index:data_index + single_repeat_num]) == data[data_index] * single_repeat_num and \
            has_target_char(data[data_index], lang):
            return True

        # 连续片段
        # use short_sizes instead of one short_size to decrease false negatives: like axbaxcaxbaxcaxbaxc
        for k, size in enumerate(short_sizes + long_sizes):
            if data_index + size > len(data):
                continue
            slice_str = " ".join(data[data_index:data_index + size])
            if slice_str not in data_map:
                data_map[slice_str] = [data_index]
            else:
                data_map[slice_str].append(data_index)

            # fix overlap case by data_map[slice_str][-1] - data_map[slice_str][-2] >= size
            if k < len(short_sizes):
                if (len(data_map[slice_str]) >= 3 and data_map[slice_str][-1] - data_map[slice_str][-2] >= size
                        and len(data) >= data_map[slice_str][-1] + data_map[slice_str][-1] - data_map[slice_str][-2]):
                    data_index_list = data_map[slice_str][-3:]
                    first_str = data[data_index_list[0]:data_index_list[1]]
                    second_str = data[data_index_list[1]:data_index_list[2]]
                    third_str = data[data_index_list[2]:(data_index_list[2] + data_index_list[2] - data_index_list[1])]
                    if first_str == second_str and second_str == third_str and \
                        has_target_char("".join(first_str), lang):
                        # continuous_res.add(first_str)
                        return True
            else:
                if (len(data_map[slice_str]) >= 2 and data_map[slice_str][-1] - data_map[slice_str][-2] >= size
                        and len(data) >= data_map[slice_str][-1] + data_map[slice_str][-1] - data_map[slice_str][-2]):
                    data_index_list = data_map[slice_str][-2:]
                    first_str = data[data_index_list[0]:data_index_list[1]]
                    second_str = data[data_index_list[1]:(data_index_list[1] + data_index_list[1] - data_index_list[0])]
                    if first_str == second_str and has_target_char("".join(first_str), lang):
                        return True
    return False


def need_detect_continuous_en(content, size=5):
    """

    :param content:
    :param size:
    :return:
    """
    data = content.split()
    data_map = dict()
    continuous_res = set()
    for data_index in range(0, len(data), size - (size - 1)):
        slice_list = data[data_index:data_index + size]
        slice_key = ' '.join(slice_list)
        if slice_key not in data_map:
            data_map[slice_key] = [data_index]
        else:
            data_map[slice_key].append(data_index)
        # 单个字的情况
        #  英文单个字的情况
        if (data_index + 6 <= len(data) and len(set(data[data_index:data_index + 6])) == 1
                and re.search(r'[a-zA-Z]', ''.join(data[data_index]))):
            continuous_res.add(data[data_index])

        # 连续片段
        if (len(data_map[slice_key]) >= 3 and data_map[slice_key][-1] - data_map[slice_key][-2] >= 5
                and len(data) >= data_map[slice_key][-1] + data_map[slice_key][-1] - data_map[slice_key][-2]):
            data_index_list = data_map[slice_key][-3:]
            first_str = data[data_index_list[0]:data_index_list[1]]
            second_str = data[data_index_list[1]:data_index_list[2]]
            third_str = data[data_index_list[2]:(data_index_list[2] + data_index_list[2] - data_index_list[1])]
            if first_str == second_str and second_str == third_str and re.search(r'[a-zA-Z]', "".join(first_str)):
                continuous_res.add(" ".join(first_str))
    return continuous_res


# if __name__ == '__main__':
#     # import ast

#     # main()
#     # main_local()
#     import random
#     text = "  (\\x -> x x) (\\x -> x x) == (\\x -> x x) (\\x -> x x)\nada"
#     # text = ' '.join([str(i) for i in range(2000)])[:10000]
#     # text = "我很高兴呀我很高兴呀我很高兴呀"

#     # text = "hahaioduowo " + (" ".join([str(random.randint(0, 9)) + "x" for i in range(250)]) + " ") * 2
#     # text = "ab x ab x ab x a a a"
#     # text = "你好我我我啦啦啦" + "新华社"*20

#     # for start, end in extract_repeated_phrases(text, 1, 250):
#     #     print(start, end, text[max(0, start-5):end+5])

#     # for start, end, repeat_end, repeat_num in find_repeated_phrases(text, 1, 250):
#     #     print(start, end, text[start:end], text[start:repeat_end], repeat_num)

#     # print(has_target_char("abcd", "en"))
#     # print(has_target_char("abcd", "ch"))
#     # print(has_target_char("新华社北京", "en"))
#     # print(has_target_char("新华社北京", "ch"))
#     # print(has_target_char("abcd北京", "en"))
#     # print(has_target_char("abcd北京", "ch"))

#     # print(need_detect_continuous_char(text, [2], [250], 6, "ch"))
#     # print(need_detect_continuous_char(text, [2], [250], 60, "en"))
#     # print(need_detect_continuous_word(text, [2], [250], 6, "en"))

#     # for record in detect_continuous_repetition(text):
#     #     mapped_start, mapped_end, mapped_repeat_end, repeat_num, char_len, word_len, is_special = record
#     #     print(
#     #         text[mapped_start:mapped_end], text[mapped_start:mapped_repeat_end],
#     #         repeat_num, char_len, word_len, is_special)

#     # for record in detect_continuous_repetition_en(text):
#     #     mapped_start, mapped_end, mapped_repeat_end, repeat_num, char_len, word_len, is_special = record
#     #     print(
#     #         text[mapped_start:mapped_end], text[mapped_start:mapped_repeat_end],
#     #         repeat_num, char_len, word_len, is_special)
#     text = """刘危安严肃的表情却松懈下来
# 嗡嗡嗡嗡嗡。
# 弓弦震动，
#     """
#     start_time = time.time()
#     result = detect_split_content(text, "cn")
#     print("time cost:", time.time() - start_time)
#     for record in result:
#         # mapped_start, mapped_end, mapped_repeat_end, repeat_num, char_len, word_len, is_special = record
#         print(json.dumps(record, ensure_ascii=False, indent=2))
#         mask_start, mask_end = record["offset"]
        
#         unit_start, unit_end = record["info"]["repeat_text_offset"]
#         repeat_num = record["score"]
#         char_len = record["info"]["char_len"]
#         word_len = record["info"]["word_len"]
#         is_special = record["info"]["is_special"]

#         print(text[unit_start:unit_end], text[mask_start:mask_end],
#             repeat_num, char_len, word_len, is_special)

#         vis_mask = f"{text[:mask_start]}【{text[mask_start:mask_end]}】{text[mask_end:]}"
#         print("=============")
#         print(vis_mask)
