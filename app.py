import streamlit as st
import pandas as pd
import re
import unicodedata
from rapidfuzz import fuzz
from tqdm import tqdm
from collections import defaultdict
import math
import io

st.set_page_config(page_title="商品定价工具", layout="wide")

# ================== 辅助函数 ==================
def detect_delimiter(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        first = f.readline()
        return '\t' if first.count('\t') > first.count(',') else ','

def read_csv_auto(file):
    if hasattr(file, 'name'):
        delim = '\t' if file.read().decode('utf-8').count('\t') > file.read().decode('utf-8').count(',') else ','
        file.seek(0)
    else:
        delim = detect_delimiter(file)
    return pd.read_csv(file, sep=delim, encoding='utf-8')

def fullwidth_to_halfwidth(text):
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
    words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', str(text))
    return [w.lower() for w in words if len(w) >= 2]

def build_inverted_index(names):
    index = defaultdict(list)
    for idx, name in enumerate(names):
        if pd.isna(name):
            continue
        tokens = set(simple_tokenizer(name))
        for token in tokens:
            index[token].append(idx)
    return index

def round_price_by_interval(value):
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
    if pd.isna(value):
        return value
    try:
        return math.ceil(value * 10) / 10
    except:
        return value

def clean_barcode(barcode):
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

def is_valid_activity_price(val):
    if pd.isna(val):
        return False
    if isinstance(val, str) and val.strip().upper() == '#N/A':
        return False
    try:
        num = float(val)
        return not pd.isna(num)
    except:
        return False

# ================== 业务逻辑函数 ==================
def match_products(internal_df, external_df, similarity_thresh, high_similarity, price_range, progress_callback=None):
    internal = internal_df.copy()
    external = external_df.copy()

    internal.rename(columns={
        '名称': 'internal_name', '商品条码': 'internal_barcode', '规格': 'internal_spec',
        '总销量': 'total_sales', '销售价': 'internal_price', '进货价': 'purchase_price'
    }, inplace=True, errors='ignore')

    external.rename(columns={
        '商品名': 'external_name', '规格': 'external_spec', '条码': 'external_barcode',
        '活动价': 'external_activity_price', '原价': 'external_original_price'
    }, inplace=True, errors='ignore')

    for col in ['internal_spec', 'total_sales', 'purchase_price']:
        if col not in internal.columns:
            internal[col] = ''
    for col in ['external_spec', 'external_activity_price']:
        if col not in external.columns:
            external[col] = ''

    internal['internal_price'] = pd.to_numeric(internal['internal_price'], errors='coerce')
    external['external_original_price'] = pd.to_numeric(external['external_original_price'], errors='coerce')
    external['external_activity_price'] = pd.to_numeric(external['external_activity_price'], errors='coerce')

    internal['internal_barcode'] = clean_barcode_series(internal['internal_barcode'])
    external['external_barcode'] = clean_barcode_series(external['external_barcode'])

    barcode_best = {}
    for barcode, group in external.groupby('external_barcode'):
        if barcode == '':
            continue
        group_sorted = group.sort_values(by='external_original_price', na_position='last')
        barcode_best[barcode] = group_sorted.iloc[0]

    external_names = external['external_name'].tolist()
    external_prices = external['external_original_price'].tolist()
    external_activity = external['external_activity_price'].tolist()
    external_specs = external['external_spec'].tolist()

    inverted_index = build_inverted_index(external_names)

    results = []
    total = len(internal)
    for i, (_, int_row) in enumerate(internal.iterrows()):
        internal_name = int_row['internal_name']
        internal_barcode = int_row['internal_barcode']
        internal_price = int_row['internal_price']
        internal_spec = int_row.get('internal_spec', '')
        total_sales = int_row.get('total_sales', '')
        purchase_price = int_row.get('purchase_price', '')

        matched = False
        match_type = '无匹配'
        matched_ext_row = None

        if internal_barcode and internal_barcode in barcode_best:
            matched_ext_row = barcode_best[internal_barcode]
            matched = True
            match_type = '条码匹配'

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

        if matched:
            row = {
                '内部商品名称': internal_name, '内部规格': internal_spec, '内部条码': internal_barcode,
                '内部销售价': internal_price, '总销量': total_sales, '进货价': purchase_price,
                '外部商品名称': matched_ext_row.get('external_name'),
                '外部规格': matched_ext_row.get('external_spec', ''),
                '外部活动价': matched_ext_row.get('external_activity_price'),
                '外部原价': matched_ext_row.get('external_original_price'),
                '匹配结果': match_type
            }
        else:
            row = {
                '内部商品名称': internal_name, '内部规格': internal_spec, '内部条码': internal_barcode,
                '内部销售价': internal_price, '总销量': total_sales, '进货价': purchase_price,
                '外部商品名称': None, '外部规格': None, '外部活动价': None, '外部原价': None, '匹配结果': '无匹配'
            }
        results.append(row)

        if progress_callback and i % 100 == 0:
            progress_callback(i + 1, total)

    return pd.DataFrame(results)

def classify_products(df, a_ratio, b_ratio):
    df = df.copy()
    df['总销量'] = pd.to_numeric(df['总销量'], errors='coerce')
    positive_mask = (df['总销量'] > 0) & (df['总销量'].notna())

    if positive_mask.sum() == 0:
        df['商品分类'] = 'C'
        return df

    sales_positive = df.loc[positive_mask, '总销量']
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

    df['商品分类'] = df['总销量'].apply(get_category)
    return df

def calculate_online_price(row, comp_adjustments, self_coeffs):
    match_result = row.get('匹配结果', '无匹配')
    category = row.get('商品分类', 'C')
    internal_sale_price = row.get('内部销售价')
    internal_purchase = row.get('进货价')
    external_original = row.get('外部原价')
    external_activity = row.get('外部活动价')

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
        return (None, None)
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

def calculate_offline_price(row, low_thresh, high_thresh, low_ratio, mid_ratio, high_ratio, fixed20, fixed15, fixed_20_barcodes, fixed_15_barcodes):
    barcode = row['内部条码']
    purchase = row['进货价']
    online_original = row['线上原价']

    if pd.isna(purchase) or purchase <= 0:
        return (None, "无有效进货价")

    if barcode in fixed_20_barcodes:
        return (purchase / fixed20, "固定毛利20%")
    if barcode in fixed_15_barcodes:
        return (purchase / fixed15, "固定毛利15%")

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

def calculate_miniprogram_price(row, base_multiplier, denom):
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

def replace_fixed_prices(df, promo_dict, retail_dict):
    df = df.copy()
    df['内部条码'] = df['内部条码'].apply(clean_barcode)
    replaced_count = 0

    for idx, row in df.iterrows():
        bc = row['内部条码']
        if not bc:
            continue
        if bc in promo_dict:
            price = promo_dict[bc]
            df.at[idx, '线下价格'] = ceil_to_one_decimal(price)
            df.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
            df.at[idx, '定价类型'] = "固定促销价"
            replaced_count += 1
        elif bc in retail_dict:
            price = retail_dict[bc]
            df.at[idx, '线下价格'] = ceil_to_one_decimal(price)
            df.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
            df.at[idx, '定价类型'] = "建议零售价"
            replaced_count += 1

    return df, replaced_count

# ================== Streamlit UI ==================
st.title("商品定价完整流程工具")
st.markdown("整合商品匹配、分类、线上/线下定价、小程序定价的完整流程")

# 初始化session state
if 'df' not in st.session_state:
    st.session_state.df = None
if 'step' not in st.session_state:
    st.session_state.step = 0

# 侧边栏 - 参数设置
st.sidebar.header("📁 数据文件上传")

internal_file = st.sidebar.file_uploader("内部数据CSV", type=['csv'], key="internal")
external_file = st.sidebar.file_uploader("外部数据CSV", type=['csv'], key="external")

st.sidebar.markdown("---")
st.sidebar.subheader("📦 可选配置文件")
margin_20_file = st.sidebar.file_uploader("线下毛利20%商品CSV（条码列）", type=['csv'], key="margin20")
margin_15_file = st.sidebar.file_uploader("线下毛利15%商品CSV（条码列）", type=['csv'], key="margin15")
promo_file = st.sidebar.file_uploader("固定促销价CSV（条码+固定促销价列）", type=['csv'], key="promo")
retail_file = st.sidebar.file_uploader("固定零售价CSV（条码+建议零售价列）", type=['csv'], key="retail")

st.sidebar.header("⚙️ 匹配参数")
similarity = st.sidebar.slider("最低相似度阈值", 20, 80, 40)
high_similarity = st.sidebar.slider("高相似度阈值", 50, 100, 75)
price_range = st.sidebar.slider("价格区间浮动比例", 0.1, 0.5, 0.35, 0.05)

st.sidebar.header("📊 分类参数")
a_ratio = st.sidebar.slider("A类占比", 0.05, 0.5, 0.2, 0.05)
b_ratio = st.sidebar.slider("B类占比", 0.3, 0.9, 0.6, 0.05)

st.sidebar.header("💰 竞品价格调整")
comp_adj = {}
for cat in ['A', 'B', 'C']:
    st.sidebar.markdown(f"**{cat}类竞品**")
    comp_adj[cat] = {
        'original': st.sidebar.number_input(f"  {cat}类原价调整", value=0.0, step=0.1, key=f"comp_orig_{cat}"),
        'activity': st.sidebar.number_input(f"  {cat}类活动价调整", value=0.0, step=0.1, key=f"comp_act_{cat}")
    }

st.sidebar.header("🏷️ 自有商品乘数")
self_coeff = {}
for cat in ['A', 'B', 'C']:
    st.sidebar.markdown(f"**{cat}类自有**")
    if cat == 'A':
        default_orig, default_act = 1.0, 1.0
    elif cat == 'B':
        default_orig, default_act = 2.5, 2.3
    else:
        default_orig, default_act = 3.5, 2.5
    self_coeff[cat] = {
        'original_coeff': st.sidebar.number_input(f"  {cat}类原价乘数", value=default_orig, step=0.1, key=f"self_orig_{cat}"),
        'activity_coeff': st.sidebar.number_input(f"  {cat}类活动价乘数", value=default_act, step=0.1, key=f"self_act_{cat}")
    }

st.sidebar.header("🏪 线下价格参数")
LOW_MARGIN_THRESHOLD = st.sidebar.number_input("低毛利阈值", value=0.10, step=0.01)
HIGH_MARGIN_THRESHOLD = st.sidebar.number_input("高毛利阈值", value=0.65, step=0.01)
LOW_MARGIN_RATIO = st.sidebar.number_input("低毛利分母", value=0.8, step=0.1)
MID_MARGIN_RATIO = st.sidebar.number_input("中毛利系数", value=0.6, step=0.1)
HIGH_MARGIN_RATIO = st.sidebar.number_input("高毛利分母", value=0.35, step=0.1)
FIXED_20_DENOM = st.sidebar.number_input("固定20%毛利分母", value=0.8, step=0.1)
FIXED_15_DENOM = st.sidebar.number_input("固定15%毛利分母", value=0.85, step=0.1)

st.sidebar.header("📱 小程序价格参数")
BASE_MULTIPLIER = st.sidebar.number_input("基准倍数", value=1.1, step=0.1)
DENOM_THRESHOLD = st.sidebar.number_input("分母阈值", value=0.8, step=0.1)

# 主内容区
tab1, tab2, tab3 = st.tabs(["🚀 开始流程", "📋 数据预览", "📥 结果下载"])

with tab1:
    st.subheader("执行商品定价流程")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("内部商品", internal_file.name if internal_file else "未上传")
    with col2:
        st.metric("外部商品", external_file.name if external_file else "未上传")

    if internal_file is None or external_file is None:
        st.warning("请上传内部数据和外部数据文件后开始执行")
    else:
        if st.button("▶️ 开始执行完整流程", type="primary", use_container_width=True):
            try:
                # Step 1: 读取数据
                with st.spinner("📂 读取数据文件..."):
                    internal_df = pd.read_csv(internal_file)
                    external_df = pd.read_csv(external_file)
                    st.session_state.step = 1

                # Step 2: 商品匹配
                progress_bar = st.progress(0)
                status_text = st.empty()
                status_text.text("匹配进度: 0%")

                def update_progress(current, total):
                    progress = int(current / total * 100)
                    progress_bar.progress(progress)
                    status_text.text(f"匹配进度: {current}/{total} ({progress}%)")

                with st.spinner("🔍 执行商品匹配..."):
                    df = match_products(internal_df, external_df, similarity, high_similarity, price_range, update_progress)
                    st.session_state.df = df
                    st.session_state.step = 2
                    progress_bar.progress(100)
                    status_text.text("匹配完成!")

                col1, col2, col3 = st.columns(3)
                col1.metric("条码匹配", len(df[df['匹配结果'] == '条码匹配']))
                col2.metric("模糊匹配", len(df[df['匹配结果'] == '模糊匹配']))
                col3.metric("无匹配", len(df[df['匹配结果'] == '无匹配']))

                st.divider()

                # Step 3: 商品分类
                with st.spinner("📊 执行商品分类..."):
                    df = classify_products(df, a_ratio, b_ratio)
                    st.session_state.df = df
                    st.session_state.step = 3

                st.success("商品分类完成")
                col1, col2, col3 = st.columns(3)
                col1.metric("A类商品", len(df[df['商品分类'] == 'A']))
                col2.metric("B类商品", len(df[df['商品分类'] == 'B']))
                col3.metric("C类商品", len(df[df['商品分类'] == 'C']))

                st.divider()

                # Step 4: 计算线上价格
                with st.spinner("💰 计算线上价格..."):
                    df['内部销售价'] = pd.to_numeric(df['内部销售价'], errors='coerce')
                    df['进货价'] = pd.to_numeric(df['进货价'], errors='coerce')
                    df['外部原价'] = pd.to_numeric(df['外部原价'], errors='coerce')
                    df['外部活动价'] = pd.to_numeric(df['外部活动价'], errors='coerce')

                    price_pairs = df.apply(lambda row: calculate_online_price(row, comp_adj, self_coeff), axis=1)
                    df['线上原价'] = price_pairs.apply(lambda x: round_price_by_interval(x[0]) if x[0] is not None else None)
                    df['线上活动价'] = price_pairs.apply(lambda x: round_price_by_interval(x[1]) if x[1] is not None and not isinstance(x[1], str) else x[1])
                    st.session_state.df = df
                    st.session_state.step = 4

                st.success(f"线上价格计算完成，有效定价: {df['线上原价'].notna().sum()}")

                st.divider()

                # Step 5: 计算线下价格
                with st.spinner("🏪 计算线下价格..."):
                    df['线上原价'] = pd.to_numeric(df['线上原价'], errors='coerce')

                    # 读取固定毛利商品条码
                    fixed_20_barcodes = set()
                    fixed_15_barcodes = set()
                    
                    if margin_20_file:
                        df_margin20 = pd.read_csv(margin_20_file)
                        if '条码' in df_margin20.columns:
                            fixed_20_barcodes = set(df_margin20['条码'].astype(str).str.strip().dropna())
                            st.info(f"已加载固定20%毛利商品：{len(fixed_20_barcodes)} 条")
                    
                    if margin_15_file:
                        df_margin15 = pd.read_csv(margin_15_file)
                        if '条码' in df_margin15.columns:
                            fixed_15_barcodes = set(df_margin15['条码'].astype(str).str.strip().dropna())
                            st.info(f"已加载固定15%毛利商品：{len(fixed_15_barcodes)} 条")

                    result = df.apply(lambda row: calculate_offline_price(row, LOW_MARGIN_THRESHOLD, HIGH_MARGIN_THRESHOLD,
                                                                          LOW_MARGIN_RATIO, MID_MARGIN_RATIO, HIGH_MARGIN_RATIO,
                                                                          FIXED_20_DENOM, FIXED_15_DENOM,
                                                                          fixed_20_barcodes, fixed_15_barcodes), axis=1)
                    df['线下价格'] = result.apply(lambda x: round_price_by_interval(x[0]) if x[0] is not None else None)
                    df['定价类型'] = result.apply(lambda x: x[1])
                    st.session_state.df = df
                    st.session_state.step = 5

                st.success(f"线下价格计算完成，有效价格: {df['线下价格'].notna().sum()}")
                st.write("定价类型分布:")
                st.write(df['定价类型'].value_counts())

                st.divider()

                # Step 6: 计算小程序价格
                with st.spinner("📱 计算小程序价格..."):
                    df['小程序价格'] = df.apply(lambda row: calculate_miniprogram_price(row, BASE_MULTIPLIER, DENOM_THRESHOLD), axis=1)
                    df['小程序价格'] = df['小程序价格'].apply(round_price_by_interval)
                    st.session_state.df = df
                    st.session_state.step = 6

                st.success(f"小程序价格计算完成，有效价格: {df['小程序价格'].notna().sum()}")

                st.divider()

                # Step 7: 替换固定价格
                with st.spinner("🏷️ 替换固定价格..."):
                    promo_dict = {}
                    retail_dict = {}
                    
                    # 读取固定促销价
                    if promo_file:
                        df_promo = pd.read_csv(promo_file)
                        df_promo.columns = df_promo.columns.str.strip()
                        if '条码' in df_promo.columns and '固定促销价' in df_promo.columns:
                            for _, r in df_promo.iterrows():
                                bc = clean_barcode(r['条码'])
                                if bc:
                                    try:
                                        promo_dict[bc] = float(r['固定促销价'])
                                    except:
                                        pass
                            st.info(f"已加载固定促销价：{len(promo_dict)} 条")
                    
                    # 读取固定零售价
                    if retail_file:
                        df_retail = pd.read_csv(retail_file)
                        df_retail.columns = df_retail.columns.str.strip()
                        if '条码' in df_retail.columns and '建议零售价' in df_retail.columns:
                            for _, r in df_retail.iterrows():
                                bc = clean_barcode(r['条码'])
                                if bc:
                                    try:
                                        retail_dict[bc] = float(r['建议零售价'])
                                    except:
                                        pass
                            st.info(f"已加载建议零售价：{len(retail_dict)} 条")

                    df, replaced = replace_fixed_prices(df, promo_dict, retail_dict)
                    st.session_state.df = df
                    st.session_state.step = 7

                st.success(f"固定价格替换完成，共替换 {replaced} 条")

                st.balloons()
                st.success("🎉 完整流程执行完成！请到「结果下载」标签页下载结果")

            except Exception as e:
                st.error(f"执行出错: {str(e)}")
                import traceback
                st.code(traceback.format_exc())

with tab2:
    st.subheader("数据预览")
    if st.session_state.df is not None:
        st.write(f"共 {len(st.session_state.df)} 条数据")
        st.dataframe(st.session_state.df.head(100), use_container_width=True)

        cols = st.multiselect("选择显示的列", st.session_state.df.columns.tolist(),
                              default=st.session_state.df.columns.tolist()[:10])
        if cols:
            st.dataframe(st.session_state.df[cols].head(100), use_container_width=True)
    else:
        st.info("暂无数据，请先执行流程")

with tab3:
    st.subheader("结果下载")
    if st.session_state.df is not None:
        csv = st.session_state.df.to_csv(index=False, encoding='utf-8-sig')
        st.download_button(
            label="📥 下载结果CSV",
            data=csv,
            file_name="商品定价结果.csv",
            mime="text/csv",
            use_container_width=True
        )

        st.divider()
        st.subheader("统计摘要")
        col1, col2, col3 = st.columns(3)
        col1.metric("总商品数", len(st.session_state.df))
        col2.metric("有效线下价格", st.session_state.df['线下价格'].notna().sum())
        col3.metric("有效小程序价格", st.session_state.df['小程序价格'].notna().sum())

        if '商品分类' in st.session_state.df.columns:
            st.write("分类分布:")
            st.write(st.session_state.df['商品分类'].value_counts())

        if '匹配结果' in st.session_state.df.columns:
            st.write("匹配结果分布:")
            st.write(st.session_state.df['匹配结果'].value_counts())

        if '定价类型' in st.session_state.df.columns:
            st.write("定价类型分布:")
            st.write(st.session_state.df['定价类型'].value_counts())
    
    st.divider()
    st.subheader("📄 上传文件模板下载")
    
    st.markdown("**必需文件模板**")
    
    internal_template = "名称,商品条码,规格,总销量,销售价,进货价\n可口可乐500ml,6902083888001,500ml,1200,3.5,2.0\n百事可乐500ml,6902083888002,500ml,800,3.5,2.0\n康师傅冰红茶500ml,6902083888003,500ml,1500,3.8,2.2"
    st.download_button(
        label="下载 内部数据 模板",
        data=internal_template,
        file_name="内部数据模板.csv",
        mime="text/csv",
        use_container_width=True
    )
    
    external_template = "商品名,规格,条码,活动价,原价\n可口可乐500毫升,500ml,6902083888001,3.0,3.5\n百事可乐500毫升,500ml,6902083888002,3.2,3.5\n康师傅冰红茶500毫升,500ml,6902083888003,3.5,3.8"
    st.download_button(
        label="下载 外部数据 模板",
        data=external_template,
        file_name="外部数据模板.csv",
        mime="text/csv",
        use_container_width=True
    )
    
    st.markdown("---")
    st.markdown("**可选配置文件模板**")
    
    margin_20_template = "条码\n6901234567890\n6901234567891\n6901234567892"
    st.download_button(
        label="下载 线下毛利20%商品 模板",
        data=margin_20_template,
        file_name="线下毛利20%商品模板.csv",
        mime="text/csv",
        use_container_width=True
    )
    
    margin_15_template = "条码\n6902234567890\n6902234567891\n6902234567892"
    st.download_button(
        label="下载 线下毛利15%商品 模板",
        data=margin_15_template,
        file_name="线下毛利15%商品模板.csv",
        mime="text/csv",
        use_container_width=True
    )
    
    promo_template = "条码,固定促销价\n6903234567890,19.9\n6903234567891,29.9\n6903234567892,39.9"
    st.download_button(
        label="下载 固定促销价 模板",
        data=promo_template,
        file_name="固定促销价模板.csv",
        mime="text/csv",
        use_container_width=True
    )
    
    retail_template = "条码,建议零售价\n6904234567890,49.9\n6904234567891,59.9\n6904234567892,69.9"
    st.download_button(
        label="下载 固定零售价 模板",
        data=retail_template,
        file_name="固定零售价模板.csv",
        mime="text/csv",
        use_container_width=True
    )
