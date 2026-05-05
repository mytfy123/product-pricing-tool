#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
商品定价完整流程脚本
整合以下6个步骤：
1. 商品匹配（条码匹配 + 模糊匹配）
2. 商品分类（A/B/C类）
3. 计算线上价格（原价、活动价）
4. 计算线下价格（含固定毛利）
5. 计算小程序价格
6. 替换固定促销价和零售价

所有参数可交互式设置，支持默认值快速运行
"""

import pandas as pd
import re
import unicodedata
from rapidfuzz import fuzz
from tqdm import tqdm
from collections import defaultdict
import math
import os

# ================== 默认配置 ==================
# 文件路径
INTERNAL_FILE = "内部数据（测试版）.csv"
EXTERNAL_FILE = "外部数据2.csv"
OUTPUT_FILE = "匹配结果_最终定价.csv"
FILE_FIXED_20 = "线下毛利20%商品.csv"
FILE_FIXED_15 = "线下毛利15%商品.csv"
PROMO_FILE = "固定促销价.csv"
RETAIL_FILE = "固定零售价.csv"

# 匹配参数
DEFAULT_SIMILARITY = 40
DEFAULT_HIGH_SIMILARITY = 75
DEFAULT_PRICE_RANGE = 0.35

# 分类参数
CLASSIFY_MODE = "nonzero_quantile"  # "nonzero_quantile" 或 "fixed_threshold"
A_RATIO = 0.2
B_RATIO = 0.6
THRESHOLD_A = 100
THRESHOLD_B = 50

# 线下价格参数
LOW_MARGIN_THRESHOLD = 0.10
HIGH_MARGIN_THRESHOLD = 0.65
LOW_MARGIN_RATIO = 0.8
MID_MARGIN_RATIO = 0.6
HIGH_MARGIN_RATIO = 0.35
FIXED_20_DENOM = 0.8
FIXED_15_DENOM = 0.85

# 小程序价格参数
BASE_MULTIPLIER = 1.1
DENOM_THRESHOLD = 0.8


# ================== 辅助函数 ==================
def get_input(prompt, default, value_type=float):
    """交互式获取输入"""
    user_input = input(prompt).strip()
    if user_input == '':
        return default
    try:
        return value_type(user_input)
    except ValueError:
        print(f"输入无效，使用默认值 {default}")
        return default


def detect_delimiter(file_path):
    """检测CSV分隔符"""
    with open(file_path, 'r', encoding='utf-8') as f:
        first = f.readline()
        return '\t' if first.count('\t') > first.count(',') else ','


def read_csv_auto(file_path):
    """自动检测分隔符读取CSV"""
    delim = detect_delimiter(file_path)
    return pd.read_csv(file_path, sep=delim, encoding='utf-8')


def fullwidth_to_halfwidth(text):
    """全角字符转半角"""
    if not isinstance(text, str):
        return text
    return unicodedata.normalize('NFKC', text)


def normalize_text(text):
    """文本标准化"""
    if pd.isna(text):
        return ""
    text = str(text)
    text = fullwidth_to_halfwidth(text)
    text = re.sub(r'[【\[\]（）\(\)].*?[】\]\)）]', '', text)
    text = text.lower().strip()
    text = re.sub(r'[^\w\s\u4e00-\u9fff]', '', text)
    return text


def get_fuzzy_score(name1, name2):
    """计算模糊匹配分数"""
    n1 = normalize_text(name1)
    n2 = normalize_text(name2)
    if not n1 or not n2:
        return 0
    if n1 in n2 or n2 in n1:
        return 100
    return fuzz.token_set_ratio(n1, n2)


def clean_barcode_series(series):
    """清洗条码列"""
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
    """简单分词"""
    words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', str(text))
    return [w.lower() for w in words if len(w) >= 2]


def build_inverted_index(names):
    """建立倒排索引"""
    index = defaultdict(list)
    for idx, name in enumerate(names):
        if pd.isna(name):
            continue
        tokens = set(simple_tokenizer(name))
        for token in tokens:
            index[token].append(idx)
    return index


def round_price_by_interval(value):
    """价格区间进位"""
    if pd.isna(value):
        return value
    sign = 1 if value >= 0 else -1
    abs_val = abs(value)
    integer_part = math.floor(abs_val)
    decimal = abs_val - integer_part
    if decimal < 0.3:
        new_decimal = 0.2
    elif decimal < 0.6:
        new_decimal = 0.5
    else:
        new_decimal = 0.9
    return sign * (integer_part + new_decimal)


def ceil_to_one_decimal(value):
    """向上取整到0.1"""
    if pd.isna(value):
        return value
    try:
        return math.ceil(value * 10) / 10
    except:
        return value


def clean_barcode(barcode):
    """清洗条码"""
    if pd.isna(barcode):
        return ''
    barcode = str(barcode).strip()
    barcode = re.sub(r'\.0$', '', barcode)
    if 'e' in barcode.lower():
        try:
            barcode = str(int(float(barcode)))
        except:
            pass
    return barcode


# ================== Step 1: 商品匹配 ==================
def match_products(internal_df, external_df, similarity_thresh, high_similarity, price_range):
    """商品匹配主函数"""
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

        # 模糊匹配
        if not matched and pd.notna(internal_price):
            tokens = set(simple_tokenizer(internal_name))
            candidate_indices = set()
            for token in tokens:
                if token in inverted_index:
                    candidate_indices.update(inverted_index[token])

            candidates = []
            for ext_idx in candidate_indices:
                ext_name = external_names[ext_idx]
                score = get_fuzzy_score(internal_name, ext_name)
                if score < similarity_thresh:
                    continue
                ext_price = external_prices[ext_idx]
                if score >= high_similarity:
                    candidates.append((ext_idx, score, ext_price))
                else:
                    if pd.notna(ext_price):
                        price_low = internal_price * (1 - price_range)
                        price_high = internal_price * (1 + price_range)
                        if price_low <= ext_price <= price_high:
                            candidates.append((ext_idx, score, ext_price))

            if candidates:
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


# ================== Step 2: 商品分类 ==================
def classify_by_nonzero_quantile(df, sales_col='总销量', a_ratio=0.2, b_ratio=0.6):
    """按非零销量数量占比分类"""
    df_result = df.copy()
    positive_mask = (df[sales_col] > 0) & (df[sales_col].notna())

    if positive_mask.sum() == 0:
        print("警告：没有销量大于0的商品，所有商品归为C类")
        df_result['商品分类'] = 'C'
        return df_result

    sales_positive = df.loc[positive_mask, sales_col]
    value_counts = sales_positive.value_counts().sort_index(ascending=False)
    total_positive = len(sales_positive)

    cum_count = 0
    a_boundary = None
    b_boundary = None

    for sales_val, cnt in value_counts.items():
        cum_count += cnt
        ratio = cum_count / total_positive
        if a_boundary is None and ratio >= a_ratio:
            a_boundary = sales_val
        if b_boundary is None and ratio >= b_ratio:
            b_boundary = sales_val
            break

    if b_boundary is None:
        b_boundary = value_counts.index.min()

    def get_category(sales):
        if pd.isna(sales) or sales <= 0:
            return 'C'
        if sales >= a_boundary:
            return 'A'
        elif sales >= b_boundary:
            return 'B'
        else:
            return 'C'

    df_result['商品分类'] = df[sales_col].apply(get_category)

    print(f"正销量商品总数：{total_positive}")
    print(f"A类边界销量（含）：{a_boundary}（累计占比 {a_ratio * 100:.1f}%）")
    print(f"B类边界销量（含）：{b_boundary}（累计占比 {b_ratio * 100:.1f}%）")
    return df_result


def classify_by_fixed_threshold(df, sales_col='总销量', a_thresh=100, b_thresh=50):
    """按固定阈值分类"""
    df_result = df.copy()

    def cat(sales):
        if pd.isna(sales) or sales < 0:
            return 'C'
        if sales >= a_thresh:
            return 'A'
        elif sales >= b_thresh:
            return 'B'
        else:
            return 'C'

    df_result['商品分类'] = df[sales_col].apply(cat)
    return df_result


# ================== Step 3: 计算线上价格 ==================
def calculate_online_price(row, comp_adjustments, self_coeffs):
    """计算线上价格"""
    match_result = row.get('匹配结果', '无匹配')
    category = row.get('商品分类', 'C')

    internal_sale_price = row.get('内部销售价')
    internal_purchase = row.get('进货价')
    external_original = row.get('外部原价')
    external_activity = row.get('外部活动价')

    # 自有商品
    if match_result == '无匹配':
        coeffs = self_coeffs.get(category, {})
        if category == 'A':
            if pd.isna(internal_sale_price):
                return (None, None)
            orig = internal_sale_price * coeffs.get('original_coeff', 1.0)
            act = internal_sale_price * coeffs.get('activity_coeff', 1.0)
            return (orig, act)
        elif category == 'B':
            if pd.isna(internal_purchase):
                return (None, None)
            orig = internal_purchase * coeffs.get('original_coeff', 2.5)
            act = internal_purchase * coeffs.get('activity_coeff', 2.3)
            return (orig, act)
        elif category == 'C':
            if pd.isna(internal_purchase):
                return (None, None)
            orig = internal_purchase * coeffs.get('original_coeff', 3.5)
            act = internal_purchase * coeffs.get('activity_coeff', 2.5)
            return (orig, act)
        else:
            return (None, None)

    # 竞品
    else:
        if pd.isna(external_original):
            return (None, None)

        if pd.isna(external_activity):
            external_activity = external_original

        adj_original = comp_adjustments.get(category, {}).get('original', 0.0)
        adj_activity = comp_adjustments.get(category, {}).get('activity', 0.0)

        if external_activity > external_original:
            online_original = external_original + adj_original
            return (online_original, "#N/A")
        else:
            online_original = external_original + adj_original
            online_activity = external_activity + adj_activity
            return (online_original, online_activity)


# ================== Step 4: 计算线下价格 ==================
def calculate_offline_price(row, low_thresh, high_thresh, low_ratio, mid_ratio, high_ratio, 
                            fixed20, fixed15, fixed_20_barcodes, fixed_15_barcodes):
    """计算线下价格"""
    barcode = row['内部条码']
    purchase = row['进货价']
    online_original = row['线上原价']
    
    if pd.isna(purchase) or purchase <= 0:
        return (None, "无有效进货价")
    
    # 固定毛利（优先级高）
    if barcode in fixed_20_barcodes:
        return (purchase / fixed20, "固定毛利20%")
    if barcode in fixed_15_barcodes:
        return (purchase / fixed15, "固定毛利15%")
    
    # 普通商品
    if pd.isna(online_original) or online_original <= 0:
        return (None, "线上原价无效")
    
    denominator = online_original * mid_ratio
    if denominator <= 0:
        return (None, "分母无效")
    
    gross_margin = (denominator - purchase) / denominator
    if gross_margin <= low_thresh:
        return (purchase / low_ratio, "普通商品(低毛利)")
    elif gross_margin < high_thresh:
        return (online_original * mid_ratio, "普通商品(中毛利)")
    else:
        return (purchase / high_ratio, "普通商品(高毛利)")


# ================== Step 5: 计算小程序价格 ==================
def is_valid_activity_price(val):
    """判断线上活动价是否有效"""
    if pd.isna(val):
        return False
    if isinstance(val, str) and val.strip().upper() == '#N/A':
        return False
    try:
        num = float(val)
        return not pd.isna(num)
    except:
        return False


def calculate_miniprogram_price(row, base_multiplier, denom):
    """计算小程序价格"""
    offline_price = row.get('线下价格')
    activity_price = row.get('线上活动价')
    purchase = row.get('进货价')

    if pd.isna(offline_price) or offline_price <= 0:
        return None

    temp = offline_price * base_multiplier

    if not is_valid_activity_price(activity_price):
        return temp
    else:
        activity_price = float(activity_price)
        if temp > activity_price:
            if pd.isna(purchase) or purchase <= 0:
                return temp
            threshold = purchase / denom
            if activity_price > threshold:
                return activity_price
            else:
                return threshold
        else:
            return temp


# ================== Step 6: 替换固定促销价和零售价 ==================
def replace_fixed_prices(df, promo_file, retail_file):
    """替换固定促销价和零售价"""
    df_result = df.copy()
    df_result['内部条码'] = df_result['内部条码'].apply(clean_barcode)

    # 读取促销价
    promo = {}
    if os.path.exists(promo_file):
        df_p = pd.read_csv(promo_file, encoding='utf-8-sig')
        df_p.columns = df_p.columns.str.strip()
        if '条码' in df_p.columns and '固定促销价' in df_p.columns:
            for _, r in df_p.iterrows():
                bc = clean_barcode(r['条码'])
                if bc:
                    try:
                        promo[bc] = float(r['固定促销价'])
                    except:
                        pass

    # 读取零售价
    retail = {}
    if os.path.exists(retail_file):
        df_r = pd.read_csv(retail_file, encoding='utf-8-sig')
        df_r.columns = df_r.columns.str.strip()
        if '条码' in df_r.columns and '建议零售价' in df_r.columns:
            for _, r in df_r.iterrows():
                bc = clean_barcode(r['条码'])
                if bc:
                    try:
                        retail[bc] = float(r['建议零售价'])
                    except:
                        pass

    # 替换
    replaced = 0
    for idx, row in df_result.iterrows():
        bc = row['内部条码']
        if not bc:
            continue
        if bc in promo:
            price = promo[bc]
            df_result.at[idx, '线下价格'] = ceil_to_one_decimal(price)
            df_result.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
            df_result.at[idx, '定价类型'] = "固定促销价"
            replaced += 1
        elif bc in retail:
            price = retail[bc]
            df_result.at[idx, '线下价格'] = ceil_to_one_decimal(price)
            df_result.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
            df_result.at[idx, '定价类型'] = "建议零售价"
            replaced += 1

    return df_result, replaced


# ================== 主流程 ==================
def main():
    print("="*60)
    print("          商品定价完整流程")
    print("="*60)
    
    # ========== Step 1: 商品匹配参数设置 ==========
    print("\n【Step 1/6】商品匹配参数设置")
    print("-" * 40)
    use_default = input(f"使用默认匹配参数 (相似度={DEFAULT_SIMILARITY}, 高相似度={DEFAULT_HIGH_SIMILARITY}, 价格区间={DEFAULT_PRICE_RANGE})？(y/n): ").strip().lower()
    if use_default == 'n':
        similarity = get_input(f"最低相似度阈值 (默认 {DEFAULT_SIMILARITY}): ", DEFAULT_SIMILARITY, int)
        high_similarity = get_input(f"高相似度阈值 (默认 {DEFAULT_HIGH_SIMILARITY}): ", DEFAULT_HIGH_SIMILARITY, int)
        price_range = get_input(f"价格区间浮动比例 (默认 {DEFAULT_PRICE_RANGE}): ", DEFAULT_PRICE_RANGE, float)
    else:
        similarity, high_similarity, price_range = DEFAULT_SIMILARITY, DEFAULT_HIGH_SIMILARITY, DEFAULT_PRICE_RANGE

    # 读取数据
    print("\n读取内部数据...")
    internal_df = read_csv_auto(INTERNAL_FILE)
    print(f"内部商品数: {len(internal_df)}")
    
    print("读取外部数据...")
    external_df = read_csv_auto(EXTERNAL_FILE)
    print(f"外部商品数: {len(external_df)}")

    # 执行匹配
    print("\n开始商品匹配...")
    df = match_products(internal_df, external_df, similarity, high_similarity, price_range)
    print("匹配统计:")
    print(df['匹配结果'].value_counts())

    # ========== Step 2: 商品分类 ==========
    print("\n【Step 2/6】商品分类")
    print("-" * 40)
    df['总销量'] = pd.to_numeric(df['总销量'], errors='coerce')
    
    use_default = input(f"使用默认分类参数 (A={A_RATIO}, B={B_RATIO})？(y/n): ").strip().lower()
    if use_default == 'n':
        a_ratio = get_input(f"A类商品数量占比 (0~1, 默认 {A_RATIO}): ", A_RATIO, float)
        b_ratio = get_input(f"B类商品数量占比 (0~1, 默认 {B_RATIO}): ", B_RATIO, float)
        if not (0 < a_ratio < b_ratio <= 1):
            print("警告：占比无效，使用默认值")
            a_ratio, b_ratio = A_RATIO, B_RATIO
    else:
        a_ratio, b_ratio = A_RATIO, B_RATIO

    df = classify_by_nonzero_quantile(df, '总销量', a_ratio, b_ratio)
    print("分类数量分布：")
    print(df['商品分类'].value_counts())

    # ========== Step 3: 计算线上价格 ==========
    print("\n【Step 3/6】计算线上价格")
    print("-" * 40)
    
    print("设置竞品价格调整值（加法）：")
    comp_adjustments = {}
    for cat in ['A', 'B', 'C']:
        use_def = input(f"  {cat}类竞品使用默认调整值 (原价+0, 活动价+0)？(y/n): ").strip().lower()
        if use_def == 'n':
            adj_orig = get_input(f"    线上原价调整值: ", 0.0)
            adj_act = get_input(f"    线上活动价调整值: ", 0.0)
        else:
            adj_orig, adj_act = 0.0, 0.0
        comp_adjustments[cat] = {'original': adj_orig, 'activity': adj_act}

    print("\n设置自有商品乘数：")
    self_coeffs = {}
    for cat in ['A', 'B', 'C']:
        if cat == 'A':
            default_orig, default_act = 1.0, 1.0
            base = "内部销售价"
        elif cat == 'B':
            default_orig, default_act = 2.5, 2.3
            base = "进货价"
        else:
            default_orig, default_act = 3.5, 2.5
            base = "进货价"
        
        use_def = input(f"  {cat}类自有商品使用默认乘数 ({base}×{default_orig}/{default_act})？(y/n): ").strip().lower()
        if use_def == 'n':
            orig_coeff = get_input(f"    线上原价乘数: ", default_orig)
            act_coeff = get_input(f"    线上活动价乘数: ", default_act)
        else:
            orig_coeff, act_coeff = default_orig, default_act
        self_coeffs[cat] = {'original_coeff': orig_coeff, 'activity_coeff': act_coeff}

    # 计算线上价格
    df['内部销售价'] = pd.to_numeric(df['内部销售价'], errors='coerce')
    df['进货价'] = pd.to_numeric(df['进货价'], errors='coerce')
    df['外部原价'] = pd.to_numeric(df['外部原价'], errors='coerce')
    df['外部活动价'] = pd.to_numeric(df['外部活动价'], errors='coerce')
    
    price_pairs = df.apply(lambda row: calculate_online_price(row, comp_adjustments, self_coeffs), axis=1)
    df['线上原价'] = price_pairs.apply(lambda x: round_price_by_interval(x[0]) if x[0] is not None else None)
    df['线上活动价'] = price_pairs.apply(lambda x: round_price_by_interval(x[1]) if x[1] is not None and not isinstance(x[1], str) else x[1])
    
    print(f"有效定价商品数（线上原价非空）：{df['线上原价'].notna().sum()}")
    print(f"活动价异常标记数：{(df['线上活动价'] == '#N/A').sum()}")

    # ========== Step 4: 计算线下价格 ==========
    print("\n【Step 4/6】计算线下价格")
    print("-" * 40)
    
    use_default = input(f"使用默认线下价格参数？(y/n): ").strip().lower()
    if use_default == 'n':
        low_thresh = get_float(f"低毛利阈值 (默认 {LOW_MARGIN_THRESHOLD}): ", LOW_MARGIN_THRESHOLD)
        high_thresh = get_float(f"高毛利阈值 (默认 {HIGH_MARGIN_THRESHOLD}): ", HIGH_MARGIN_THRESHOLD)
        low_ratio = get_float(f"低毛利分母 (默认 {LOW_MARGIN_RATIO}): ", LOW_MARGIN_RATIO)
        mid_ratio = get_float(f"中毛利系数 (默认 {MID_MARGIN_RATIO}): ", MID_MARGIN_RATIO)
        high_ratio = get_float(f"高毛利分母 (默认 {HIGH_MARGIN_RATIO}): ", HIGH_MARGIN_RATIO)
        fixed20 = get_float(f"固定20%毛利分母 (默认 {FIXED_20_DENOM}): ", FIXED_20_DENOM)
        fixed15 = get_float(f"固定15%毛利分母 (默认 {FIXED_15_DENOM}): ", FIXED_15_DENOM)
    else:
        low_thresh, high_thresh = LOW_MARGIN_THRESHOLD, HIGH_MARGIN_THRESHOLD
        low_ratio, mid_ratio, high_ratio = LOW_MARGIN_RATIO, MID_MARGIN_RATIO, HIGH_MARGIN_RATIO
        fixed20, fixed15 = FIXED_20_DENOM, FIXED_15_DENOM

    # 读取固定毛利商品
    fixed_20_barcodes = set()
    fixed_15_barcodes = set()
    if os.path.exists(FILE_FIXED_20):
        df20 = pd.read_csv(FILE_FIXED_20, encoding='utf-8-sig')
        if '条码' in df20.columns:
            fixed_20_barcodes = set(df20['条码'].astype(str).str.strip())
            print(f"已加载固定20%毛利商品：{len(fixed_20_barcodes)} 条")
    if os.path.exists(FILE_FIXED_15):
        df15 = pd.read_csv(FILE_FIXED_15, encoding='utf-8-sig')
        if '条码' in df15.columns:
            fixed_15_barcodes = set(df15['条码'].astype(str).str.strip())
            print(f"已加载固定15%毛利商品：{len(fixed_15_barcodes)} 条")

    # 计算线下价格
    result = df.apply(lambda row: calculate_offline_price(row, low_thresh, high_thresh, low_ratio, 
                                                          mid_ratio, high_ratio, fixed20, fixed15, 
                                                          fixed_20_barcodes, fixed_15_barcodes), axis=1)
    df['线下价格'] = result.apply(lambda x: round_price_by_interval(x[0]))
    df['定价类型'] = result.apply(lambda x: x[1])
    
    print(f"有效线下价格商品数：{df['线下价格'].notna().sum()}")
    print("定价类型分布：")
    print(df['定价类型'].value_counts(dropna=False))

    # ========== Step 5: 计算小程序价格 ==========
    print("\n【Step 5/6】计算小程序价格")
    print("-" * 40)
    
    use_default = input(f"使用默认小程序价格参数 (基准倍数={BASE_MULTIPLIER}, 分母阈值={DENOM_THRESHOLD})？(y/n): ").strip().lower()
    if use_default == 'n':
        base_multiplier = get_float(f"线下价格 × 倍数（基准）: ", BASE_MULTIPLIER)
        denom = get_float(f"进货价 / 分母阈值: ", DENOM_THRESHOLD)
    else:
        base_multiplier, denom = BASE_MULTIPLIER, DENOM_THRESHOLD

    df['线下价格'] = pd.to_numeric(df['线下价格'], errors='coerce')
    df['小程序价格'] = df.apply(lambda row: calculate_miniprogram_price(row, base_multiplier, denom), axis=1)
    df['小程序价格'] = df['小程序价格'].apply(round_price_by_interval)
    
    print(f"有效小程序价格商品数：{df['小程序价格'].notna().sum()}")

    # ========== Step 6: 替换固定促销价和零售价 ==========
    print("\n【Step 6/6】替换固定促销价和零售价")
    print("-" * 40)
    
    df, replaced = replace_fixed_prices(df, PROMO_FILE, RETAIL_FILE)
    print(f"共替换 {replaced} 条商品")

    # 保存最终结果
    df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
    print(f"\n{'='*60}")
    print(f"完整流程执行完成！")
    print(f"结果保存至：{OUTPUT_FILE}")
    print(f"总商品数：{len(df)}")
    print(f"有效线下价格：{df['线下价格'].notna().sum()}")
    print(f"有效小程序价格：{df['小程序价格'].notna().sum()}")
    print("="*60)


def get_float(prompt, default):
    """获取浮点数输入"""
    return get_input(prompt, default, float)


if __name__ == '__main__':
    main()
