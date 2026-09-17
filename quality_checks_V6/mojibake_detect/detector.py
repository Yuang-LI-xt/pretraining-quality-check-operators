#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced MojibakeDetector

V6.0 策略：
  - 不因为阿拉伯文/印度文等复杂脚本整条判废
  - 只输出字符级/片段级命中，上层按需清洗，不做整条删除决策
  - PUA 仍以部分修复/已知噪声删除为清洗口径，不做全范围删除
  - 组合符只按 V6 装饰白名单命中，避免按 Mn/Me 类别误伤多语种

新增规则（参考 others）：
  - C1 控制字符检测 (0x80-0x9F)
  - 双重编码结构化检测（阈值机制）
  - PUA 私用区洪泛检测
  - 零宽字符洪泛检测
  - 不可见垃圾字符检测 (BOM/WORD JOINER等)
  - Specials 区段检测 (U+FFF0-U+FFFC)
  - 行间标注字符检测 (U+FFF9-U+FFFB)
  - HTML 实体残留检测
  - 双向文本控制符检测
  - 装饰符号/颜文字组合符检测

保留原有规则：
  - FFFD 替换符检测
  - 中文乱码序列模式匹配（GBK/UTF-8/Latin-1 互相误解码）
  - Cs/Cn 字符检测（可选开启）
"""
import numpy as np
import mojibake_detect.custom_patterns as custom_patterns
import mojibake_detect.utils as utils


# ---------------------------------------------------------------------------
# 阈值配置
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "double_enc": 5,         # 双重编码匹配数阈值
    "cp1252": 3,             # CP1252 乱码匹配数阈值
    "zw_flood": 1,           # 零宽字符出现 ≥ 1 即命中（业务场景：emoji/小语种都要清）
    "invis_garbage": 3,      # 不可见垃圾字符容忍上限
    "pua": 5,                # PUA 私用区字符容忍上限
    "conditional_zw": 1,     # 条件性零宽字符出现 ≥ 1 即命中（无豁免）
    "math_symbol_flood": 8,   # 同一数学/符号字符连续重复阈值
    "control_format_flood": 3, # 格式/行分隔控制字符洪泛阈值
}


class MojibakeDetector(object):
    """Enhanced mojibake detect tool"""

    def __init__(self, enable_cscn=True, thresholds=None):
        """
        initialize

        Args:
            enable_cscn: 是否启用 Cs/Cn 字符检测（默认开启）
            thresholds: 自定义阈值字典，可覆盖默认值
        """
        self.enable_cscn = enable_cscn
        self.thresholds = dict(THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)

    # -----------------------------------------------------------------------
    # 原有规则（保留）
    # -----------------------------------------------------------------------

    def get_fffd_hit(self, text):
        """规则: U+FFFD 替换符检测"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_fffd(char):
                result[i] = True
        return result

    def get_cscn_hit(self, text):
        """规则: Cs/Cn 字符检测（代理对/未分配码位）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_cscn(char):
                result[i] = True
        return result

    def get_pattern_hit(self, text):
        """规则: 中文乱码序列模式匹配"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        # 子规则A: 单次命中即判定
        hit = custom_patterns.garbled_seq_pattern_onehit.finditer(text)
        for h in hit:
            start, end = h.span()
            result[start:end] = True

        # 子规则B: 需要两种以上不同字符命中
        hit2 = list(custom_patterns.garbled_seq_pattern_twohits.finditer(text))
        hit_chars = set([text[h.start()] for h in hit2])
        if len(hit_chars) >= 2:
            for h in hit2:
                result[h.start()] = True

        return result

    # -----------------------------------------------------------------------
    # 新增规则
    # -----------------------------------------------------------------------

    def get_c1_control_hit(self, text):
        """规则: C1 控制字符检测 (0x80-0x9F)，任何语言都不应出现"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_c1_control(char):
                result[i] = True
        return result

    def get_double_enc_hit(self, text):
        """规则: 双重编码结构化检测（带阈值）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        all_matches = []
        for pattern in (custom_patterns.double_enc_2byte,
                        custom_patterns.double_enc_3byte,
                        custom_patterns.double_enc_4byte):
            for m in pattern.finditer(text):
                all_matches.append(m.span())

        # 只有匹配数达到阈值才标记
        if len(all_matches) >= self.thresholds["double_enc"]:
            for start, end in all_matches:
                result[start:end] = True

        return result

    def get_cp1252_hit(self, text):
        """规则: CP1252 乱码检测（带阈值）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        matches = list(custom_patterns.cp1252_mojibake.finditer(text))

        # 只有匹配数达到阈值才标记
        if len(matches) >= self.thresholds["cp1252"]:
            for m in matches:
                start, end = m.span()
                result[start:end] = True

        return result

    def get_pua_hit(self, text):
        """规则: PUA 私用区洪泛检测"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        pua_count = 0
        for i, char in enumerate(text):
            if utils.is_pua(char):
                result[i] = True
                pua_count += 1

        # 未达到阈值则清除标记
        if pua_count < self.thresholds["pua"]:
            result[:] = False

        return result

    def get_pua_known_noise_hit(self, text):
        """规则: 已知 PUA 噪声字符检测（零容忍，非全范围 PUA 删除）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_known_pua_noise(char):
                result[i] = True
        return result

    def get_html_entity_hit(self, text):
        """规则: HTML named/decimal/hex entity residue detection"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for match in custom_patterns.html_entity.finditer(text):
            start, end = match.span()
            result[start:end] = True
        return result

    def get_bidi_control_hit(self, text):
        """规则: 双向文本控制符检测"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_bidi_control(char):
                result[i] = True
        return result

    def get_decorative_symbol_hit(self, text):
        """规则: 颜文字/装饰符号检测"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_decorative_symbol(char):
                result[i] = True
        return result

    def get_decorative_combining_hit(self, text):
        """规则: V6 白名单颜文字组合符检测"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_decorative_combining(char):
                result[i] = True
        return result

    def get_zw_flood_hit(self, text):
        """规则: 零宽字符洪泛检测"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        zw_count = 0
        for i, char in enumerate(text):
            if char in utils.ALL_ZW_CHARS:
                result[i] = True
                zw_count += 1

        # 未达到阈值则清除标记
        if zw_count < self.thresholds["zw_flood"]:
            result[:] = False

        return result

    def get_invis_garbage_hit(self, text):
        """规则: 不可见垃圾字符检测 (BOM/WORD JOINER等混入正文)"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        garbage_count = 0
        for i, char in enumerate(text):
            if char in utils.INVIS_ALWAYS_GARBAGE:
                result[i] = True
                garbage_count += 1

        # 未达到阈值则清除标记
        if garbage_count < self.thresholds["invis_garbage"]:
            result[:] = False

        return result

    def get_specials_hit(self, text):
        """规则: Specials 区段 + 行间标注字符检测（零容忍）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        for i, char in enumerate(text):
            if char in utils.SPECIALS_GARBAGE or char in utils.INTERLINEAR_CHARS:
                result[i] = True

        return result

    def get_conditional_zw_hit(self, text):
        """规则: 条件性零宽字符检测（无豁免，阈值=1）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)

        zw_count = 0
        for i, char in enumerate(text):
            if char in utils.CONDITIONAL_ZW:
                result[i] = True
                zw_count += 1

        if zw_count < self.thresholds["conditional_zw"]:
            result[:] = False

        return result

    def get_variation_selector_hit(self, text):
        """规则: Unicode variation selector 检测（FE00-FE0F / E0100-E01EF）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_variation_selector(char):
                result[i] = True
        return result

    def get_styled_math_alnum_hit(self, text):
        """规则: 数学样式字母数字字符检测（U+1D400-U+1D7FF）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_styled_math_alnum(char):
                result[i] = True
        return result

    def get_abnormal_space_hit(self, text):
        """规则: 异常空格检测（NBSP/NARROW NBSP/EM SPACE/全角空格/THIN SPACE）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        for i, char in enumerate(text):
            if utils.is_abnormal_space(char):
                result[i] = True
        return result

    def get_math_symbol_flood_hit(self, text):
        """规则: 数学/符号字符连续洪泛检测"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        threshold = self.thresholds["math_symbol_flood"]
        run_start = None
        run_char = None
        run_len = 0

        def mark_run():
            if run_start is not None and run_len >= threshold:
                result[run_start:run_start + run_len] = True

        for i, char in enumerate(text):
            if utils.is_math_flood_symbol(char):
                if char == run_char:
                    run_len += 1
                else:
                    mark_run()
                    run_start = i
                    run_char = char
                    run_len = 1
            else:
                mark_run()
                run_start = None
                run_char = None
                run_len = 0
        mark_run()
        return result

    def get_control_format_flood_hit(self, text):
        """规则: 格式/行分隔控制字符洪泛检测（Cf/Zl/Zp）"""
        text_len = len(text)
        result = np.array([False] * text_len, dtype=bool)
        positions = []
        for i, char in enumerate(text):
            if utils.is_control_format_char(char):
                positions.append(i)
        if len(positions) >= self.thresholds["control_format_flood"]:
            for i in positions:
                result[i] = True
        return result

    # -----------------------------------------------------------------------
    # 结果组织
    # -----------------------------------------------------------------------

    def reorganize(self, window_result, text_len):
        """reorganize hit result for a window"""
        new_result = {}
        has_hit = False
        for k, v in window_result.items():
            hit_char_len = int(v.sum())
            if hit_char_len > 0:
                has_hit = True
            new_result[k] = {
                "hit_num": hit_char_len,
                "hit_ratio": hit_char_len / text_len if text_len > 0 else 0,
                "hit_positions": ''.join('1' if x else '0' for x in v) if hit_char_len > 0 else None
            }
        return new_result, has_hit

    def get_old_rule_hits(self, text):
        """原有规则：按窗口检测"""
        window_result = {}
        window_result["fffd"] = self.get_fffd_hit(text)
        window_result["seq"] = self.get_pattern_hit(text)
        if self.enable_cscn:
            window_result["cscn"] = self.get_cscn_hit(text)
        return window_result

    def get_new_rule_hits(self, text):
        """新增规则：对全文整体检测"""
        result = {}
        result["c1_control"] = self.get_c1_control_hit(text)
        result["double_enc"] = self.get_double_enc_hit(text)
        result["cp1252"] = self.get_cp1252_hit(text)
        result["pua"] = self.get_pua_hit(text)
        result["pua_known_noise"] = self.get_pua_known_noise_hit(text)
        result["html_entity"] = self.get_html_entity_hit(text)
        result["bidi_control"] = self.get_bidi_control_hit(text)
        result["decorative_symbol"] = self.get_decorative_symbol_hit(text)
        result["decorative_combining"] = self.get_decorative_combining_hit(text)
        result["zw_flood"] = self.get_zw_flood_hit(text)
        result["invis_garbage"] = self.get_invis_garbage_hit(text)
        result["specials"] = self.get_specials_hit(text)
        result["conditional_zw"] = self.get_conditional_zw_hit(text)
        result["variation_selector"] = self.get_variation_selector_hit(text)
        result["styled_math_alnum"] = self.get_styled_math_alnum_hit(text)
        result["abnormal_space"] = self.get_abnormal_space_hit(text)
        result["math_symbol_flood"] = self.get_math_symbol_flood_hit(text)
        result["control_format_flood"] = self.get_control_format_flood_hit(text)
        return result

    def split_content(self, content):
        """split content to windows by new lines"""
        paras = content.split('\n')
        windows = []
        num = len(paras)
        for i, para in enumerate(paras):
            if i == num - 1:
                windows.append(para)
            else:
                windows.append(para + '\n')
        return windows

    def exec_text(self, text):
        """entrance function: execute detection on a text"""
        hits = []

        # 新增规则：阈值在全文级别判定，得到全文布尔数组
        fulltext_arrays = self.get_new_rule_hits(text)

        # 按窗口遍历，合并原有规则（窗口级）+ 新增规则（全文级切片）
        windows = self.split_content(text)
        start = 0
        for _, window in enumerate(windows):
            end = start + len(window)
            if window.strip():
                # 原有规则：在窗口上检测
                window_result = self.get_old_rule_hits(window)
                # 新增规则：从全文数组中切出当前窗口的片段
                for key, arr in fulltext_arrays.items():
                    window_result[key] = arr[start:end]
                window_result, has_hit = self.reorganize(window_result, len(window))
                if has_hit:
                    hits.append({
                        "offset": [start, end],
                        "detail": window_result
                    })
            start = end

        result = {"hits": hits}
        return result


if __name__ == "__main__":
    detector = MojibakeDetector()
    import json
    with open("mojibake_detect/testin", "r") as fin:
        text_input = json.loads(fin.read())["tgt"]
    pred_result = detector.exec_text(text_input)
    print(json.dumps(pred_result, ensure_ascii=False))
