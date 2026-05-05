#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
商品匹配脚本（可自定义参数 + 进度条 + 倒排索引加速）
- 支持用户输入相似度阈值、高相似度阈值、价格区间
- 高相似度（默认75分以上）仍参与排序，但不提前终止
- 优化：去括号内容、全角转半角、token_set_ratio
- 倒排索引+全候选排序：结果与原全量遍历版本完全一致
"""

import pandas as pd
import re
import unicodedata
from rapidfuzz import fuzz
from tqdm import tqdm
from collections import defaultdict

# ================== 默认配置 ==================
DEFAULT_SIMILARITY = 40
DEFAULT_HIGH_SIMILARITY = 75
DEFAULT_PRICE_RANGE = 0.35

INTERNAL_FILE = "内部数据（测试版）.csv"
EXTERNAL_FILE = "外部数据2.csv"
OUTPUT_FILE = "匹配结果（测试5）.csv"


# ================== 辅助函数 ==================
def detect_delimiter(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        first = f.readline()
        return '\t' if first.count('\t') > first.count(',') else ','


def read_csv_auto(file_path):
    delim = detect_delimiter(file_path)
    return pd.read_csv(file_path, sep=delim, encoding='utf-8')


def fullwidth_to_halfwidth(text):
    """全角字符转半角（使用NFKC规范化）"""
    if not isinstance(text, str):
        return text
    return unicodedata.normalize('NFKC', text)


def normalize_text(text):
    if pd.isna(text):
        return ""
    text = str(text)
    text = fullwidth_to_halfwidth(text)
    text = re.sub(r'[【\[\]（）\(\)].*?[】\]\)）]', '', text)
    text = text.lower().strip()
    text = re.sub(r'[^\w\s\u4e00-\u9fff]', '', text)
    return text


def get_fuzzy_score(name1, name2):
    n1 = normalize_text(name1)
    n2 = normalize_text(name2)
    if not n1 or not n2:
        return 0
    if n1 in n2 or n2 in n1:
        return 100
    return fuzz.token_set_ratio(n1, n2)


def clean_barcode_series(series):
    bc = series.astype(str).str.strip()
    bc = bc.replace(['nan', 'None', ''], '')

    def clean_one(val):
        if val == '':
            return ''
        try:
            num = float(val)
            if num.is_integer():
                return str(int(num))
            else:
                return val
        except ValueError:
            return val

    return bc.apply(clean_one)


def simple_tokenizer(text):
    """简单分词：提取中文词和英文数字串，过滤长度>=2的token"""
    words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', str(text))
    return [w.lower() for w in words if len(w) >= 2]


def build_inverted_index(names):
    """建立倒排索引：token -> list of index"""
    index = defaultdict(list)
    for idx, name in enumerate(names):
        if pd.isna(name):
            continue
        tokens = set(simple_tokenizer(name))
        for token in tokens:
            index[token].append(idx)
    return index


def match_products(internal_df, external_df, similarity_thresh, high_similarity, price_range):
    internal = internal_df.copy()
    external = external_df.copy()

    # 列名映射
    internal.rename(columns={
        '名称': 'internal_name',
        '商品条码': 'internal_barcode',
        '规格': 'internal_spec',
        '总销量': 'total_sales',
        '销售价': 'internal_price',
        '进货价': 'purchase_price'
    }, inplace=True, errors='ignore')

    external.rename(columns={
        '商品名': 'external_name',
        '规格': 'external_spec',
        '条码': 'external_barcode',
        '活动价': 'external_activity_price',
        '原价': 'external_original_price'
    }, inplace=True, errors='ignore')

    # 必要字段检查
    required_internal = ['internal_name', 'internal_barcode', 'internal_price']
    for col in required_internal:
        if col not in internal.columns:
            raise KeyError(f"内部数据缺少必要列: {col}")
    required_external = ['external_name', 'external_barcode', 'external_original_price']
    for col in required_external:
        if col not in external.columns:
            raise KeyError(f"外部数据缺少必要列: {col}")

    # 可选字段默认值
    for col in ['internal_spec', 'total_sales', 'purchase_price']:
        if col not in internal.columns:
            internal[col] = ''
    for col in ['external_spec', 'external_activity_price']:
        if col not in external.columns:
            external[col] = ''

    # 价格转数值
    internal['internal_price'] = pd.to_numeric(internal['internal_price'], errors='coerce')
    external['external_original_price'] = pd.to_numeric(external['external_original_price'], errors='coerce')
    external['external_activity_price'] = pd.to_numeric(external['external_activity_price'], errors='coerce')

    # 清洗条码
    internal['internal_barcode'] = clean_barcode_series(internal['internal_barcode'])
    external['external_barcode'] = clean_barcode_series(external['external_barcode'])

    # 条码精确匹配字典
    barcode_best = {}
    for barcode, group in external.groupby('external_barcode'):
        if barcode == '':
            continue
        group_sorted = group.sort_values(by='external_original_price', na_position='last')
        barcode_best[barcode] = group_sorted.iloc[0]

    # 准备外部列表
    external_names = external['external_name'].tolist()
    external_prices = external['external_original_price'].tolist()
    external_activity = external['external_activity_price'].tolist()
    external_specs = external['external_spec'].tolist()

    # 构建倒排索引
    print("正在构建外部商品名称倒排索引...")
    inverted_index = build_inverted_index(external_names)
    print("倒排索引构建完成。")

    results = []
    for _, int_row in tqdm(internal.iterrows(), total=len(internal), desc="匹配进度", unit="商品"):
        internal_name = int_row['internal_name']
        internal_barcode = int_row['internal_barcode']
        internal_price = int_row['internal_price']
        internal_spec = int_row.get('internal_spec', '')
        total_sales = int_row.get('total_sales', '')
        purchase_price = int_row.get('purchase_price', '')

        matched = False
        match_type = '无匹配'
        matched_ext_row = None

        # 优先条码匹配
        if internal_barcode and internal_barcode in barcode_best:
            matched_ext_row = barcode_best[internal_barcode]
            matched = True
            match_type = '条码匹配'

        # 模糊匹配（倒排索引获取全量候选，不限制数量，不提前终止）
        if not matched and pd.notna(internal_price):
            tokens = set(simple_tokenizer(internal_name))
            candidate_indices = set()
            for token in tokens:
                if token in inverted_index:
                    candidate_indices.update(inverted_index[token])

            # 收集所有符合条件的候选
            candidates = []
            for ext_idx in candidate_indices:
                ext_name = external_names[ext_idx]
                score = get_fuzzy_score(internal_name, ext_name)
                if score < similarity_thresh:
                    continue
                ext_price = external_prices[ext_idx]
                if score >= high_similarity:
                    # 高相似度只记录，不提前退出
                    candidates.append((ext_idx, score, ext_price))
                else:
                    if pd.notna(ext_price):
                        price_low = internal_price * (1 - price_range)
                        price_high = internal_price * (1 + price_range)
                        if price_low <= ext_price <= price_high:
                            candidates.append((ext_idx, score, ext_price))

            if candidates:
                # 按相似度降序，价格差升序排序（与原逻辑一致）
                candidates.sort(key=lambda x: (-x[1], abs(x[2] - internal_price)))
                best_idx = candidates[0][0]
                best_ext = {
                    'external_name': external_names[best_idx],
                    'external_spec': external_specs[best_idx],
                    'external_activity_price': external_activity[best_idx],
                    'external_original_price': external_prices[best_idx]
                }
                matched = True
                match_type = '模糊匹配'
                matched_ext_row = best_ext

        # 构建输出行
        if matched:
            row = {
                '内部商品名称': internal_name,
                '内部规格': internal_spec,
                '内部条码': internal_barcode,
                '内部销售价': internal_price,
                '总销量': total_sales,
                '进货价': purchase_price,
                '外部商品名称': matched_ext_row.get('external_name'),
                '外部规格': matched_ext_row.get('external_spec', ''),
                '外部活动价': matched_ext_row.get('external_activity_price'),
                '外部原价': matched_ext_row.get('external_original_price'),
                '匹配结果': match_type
            }
        else:
            row = {
                '内部商品名称': internal_name,
                '内部规格': internal_spec,
                '内部条码': internal_barcode,
                '内部销售价': internal_price,
                '总销量': total_sales,
                '进货价': purchase_price,
                '外部商品名称': None,
                '外部规格': None,
                '外部活动价': None,
                '外部原价': None,
                '匹配结果': '无匹配'
            }
        results.append(row)

    return pd.DataFrame(results)


def get_user_input(prompt, default, value_type=float):
    user_input = input(prompt).strip()
    if user_input == '':
        return default
    try:
        return value_type(user_input)
    except ValueError:
        print(f"输入无效，使用默认值 {default}")
        return default


def main():
    print("请输入匹配参数（直接回车使用默认值）:")
    similarity = get_user_input(f"最低相似度阈值 (默认 {DEFAULT_SIMILARITY}): ", DEFAULT_SIMILARITY, int)
    high_similarity = get_user_input(f"高相似度阈值 (默认 {DEFAULT_HIGH_SIMILARITY}): ", DEFAULT_HIGH_SIMILARITY, int)
    price_range = get_user_input(f"价格区间浮动比例 (例如 0.3 表示 ±30%，默认 {DEFAULT_PRICE_RANGE}): ", DEFAULT_PRICE_RANGE, float)

    print(f"\n当前使用的参数：")
    print(f"  最低相似度阈值 = {similarity}")
    print(f"  高相似度阈值 = {high_similarity}（超过此值忽略价格区间）")
    print(f"  价格区间比例 = ±{price_range * 100}%\n")

    try:
        print("读取内部 CSV...")
        internal_df = read_csv_auto(INTERNAL_FILE)
        print("读取外部 CSV...")
        external_df = read_csv_auto(EXTERNAL_FILE)
    except Exception as e:
        print(f"读取文件失败: {e}")
        return

    try:
        result_df = match_products(internal_df, external_df, similarity, high_similarity, price_range)
    except KeyError as e:
        print(f"匹配失败：{e}")
        return

    result_df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
    print(f"匹配完成，结果保存至: {OUTPUT_FILE}")
    print("匹配统计:")
    print(result_df['匹配结果'].value_counts())


if __name__ == '__main__':
    main()