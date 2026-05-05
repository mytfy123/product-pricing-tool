#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
价格计算脚本（自定义进位规则 + 可分别调节原价/活动价）
- 进位规则：小数部分 [0.0,0.3)->.2, [0.3,0.6)->.5, [0.6,1.0)->.9
- 竞品各分类的线上原价和线上活动价可独立设置调整值（如 +0.1, -0.2 等）
- 自有商品各分类的线上原价和线上活动价可独立设置乘数（乘以内部销售价或进货价）
"""

import pandas as pd
import sys
import os
import math

def round_price_by_interval(value):
    """
    将价格按小数部分区间映射：
    [0.0, 0.3) -> 加 0.2 (实际为 floor + 0.2)
    [0.3, 0.6) -> 加 0.5
    [0.6, 1.0) -> 加 0.9
    """
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
    result = sign * (integer_part + new_decimal)
    return result

def get_float(prompt, default=0.0):
    """交互式获取浮点数输入"""
    user_input = input(prompt).strip()
    if user_input == "":
        return default
    try:
        return float(user_input)
    except ValueError:
        print(f"输入无效，使用默认值 {default}")
        return default

def calculate_price(row, comp_adjustments, self_coeffs):
    """
    comp_adjustments: dict {'A': {'original': adj_orig, 'activity': adj_act}, ...}
    self_coeffs: dict {'A': {'original_coeff': coeff, 'activity_coeff': coeff},
                       'B': {'original_coeff': coeff, 'activity_coeff': coeff},
                       'C': {...}}
    """
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
                print(f"警告：自有A类商品缺少内部销售价，行索引 {row.name}")
                return (None, None)
            orig = internal_sale_price * coeffs.get('original_coeff', 1.0)
            act = internal_sale_price * coeffs.get('activity_coeff', 1.0)
            return (orig, act)
        elif category == 'B':
            if pd.isna(internal_purchase):
                print(f"警告：自有B类商品缺少进货价，行索引 {row.name}")
                return (None, None)
            orig = internal_purchase * coeffs.get('original_coeff', 2.5)
            act = internal_purchase * coeffs.get('activity_coeff', 2.3)
            return (orig, act)
        elif category == 'C':
            if pd.isna(internal_purchase):
                print(f"警告：自有C类商品缺少进货价，行索引 {row.name}")
                return (None, None)
            orig = internal_purchase * coeffs.get('original_coeff', 3.5)
            act = internal_purchase * coeffs.get('activity_coeff', 2.5)
            return (orig, act)
        else:
            return (None, None)

    # 竞品
    else:
        if pd.isna(external_original):
            print(f"警告：竞品缺少外部原价，行索引 {row.name}")
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

def main():
    input_file = "匹配结果_分类.csv"
    output_file = "匹配结果_定价.csv"

    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    if not os.path.exists(input_file):
        print(f"错误：文件 {input_file} 不存在")
        return

    df = pd.read_csv(input_file, encoding='utf-8-sig')

    required_cols = ['匹配结果', '商品分类', '内部销售价', '进货价', '外部原价', '外部活动价']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"错误：输入文件缺少以下列：{missing}")
        print("实际列：", df.columns.tolist())
        return

    for col in ['内部销售价', '进货价', '外部原价', '外部活动价']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # ========== 1. 竞品价格调整值（加法） ==========
    print("\n=== 设置竞品各分类的线上原价和线上活动价调整值 ===")
    print("（例如 +0.1 表示在原价/活动价基础上加0.1，-0.2 表示减0.2）\n")
    comp_adjustments = {}
    for cat in ['A', 'B', 'C']:
        print(f"----- {cat} 类商品（竞品） -----")
        adj_orig = get_float(f"  线上原价调整值 (默认 0.0): ", 0.0)
        adj_act = get_float(f"  线上活动价调整值 (默认 0.0): ", 0.0)
        comp_adjustments[cat] = {'original': adj_orig, 'activity': adj_act}

    # ========== 2. 自有商品乘数 ==========
    print("\n=== 设置自有商品各分类的线上原价和线上活动价乘数 ===")
    print("（A类乘数作用于「内部销售价」，B/C类乘数作用于「进货价」）\n")
    self_coeffs = {}
    for cat in ['A', 'B', 'C']:
        print(f"----- {cat} 类商品（自有） -----")
        if cat == 'A':
            default_orig = 1.0
            default_act = 1.0
            base = "内部销售价"
        elif cat == 'B':
            default_orig = 2.5
            default_act = 2.3
            base = "进货价"
        else:  # C
            default_orig = 3.5
            default_act = 2.5
            base = "进货价"
        orig_coeff = get_float(f"  线上原价乘数（{base} × ?），默认 {default_orig}: ", default_orig)
        act_coeff = get_float(f"  线上活动价乘数（{base} × ?），默认 {default_act}: ", default_act)
        self_coeffs[cat] = {'original_coeff': orig_coeff, 'activity_coeff': act_coeff}

    # 显示汇总
    print("\n当前设置汇总：")
    print("【竞品】")
    for cat, vals in comp_adjustments.items():
        print(f"  {cat}类: 原价+{vals['original']}, 活动价+{vals['activity']}")
    print("【自有商品】")
    for cat, vals in self_coeffs.items():
        if cat == 'A':
            print(f"  {cat}类: 原价 = 内部销售价 × {vals['original_coeff']}, 活动价 = 内部销售价 × {vals['activity_coeff']}")
        else:
            print(f"  {cat}类: 原价 = 进货价 × {vals['original_coeff']}, 活动价 = 进货价 × {vals['activity_coeff']}")
    print()

    # 计算原始价格
    price_pairs = df.apply(lambda row: calculate_price(row, comp_adjustments, self_coeffs), axis=1)
    df['线上原价_raw'] = price_pairs.apply(lambda x: x[0])
    df['线上活动价_raw'] = price_pairs.apply(lambda x: x[1])

    # 应用进位规则
    df['线上原价'] = df['线上原价_raw'].apply(lambda x: round_price_by_interval(x) if not isinstance(x, str) else x)
    df['线上活动价'] = df['线上活动价_raw'].apply(lambda x: round_price_by_interval(x) if not isinstance(x, str) else x)

    df.drop(['线上原价_raw', '线上活动价_raw'], axis=1, inplace=True)

    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"价格计算完成，结果保存至：{output_file}")
    print(f"有效定价商品数（线上原价非空）：{df['线上原价'].notna().sum()}")
    print(f"活动价异常标记数：{(df['线上活动价'] == '#N/A').sum()}")

if __name__ == '__main__':
    main()