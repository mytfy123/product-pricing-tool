#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
线下价格计算脚本（可自定义系数 + 区间进位）
- 可自定义：毛利阈值、折扣系数、固定毛利分母等
- 进位规则：小数部分 [0.0,0.3)->0.2, [0.3,0.6)->0.5, [0.6,1.0)->0.9
- 输出：原文件增加“线下价格”和“定价类型”列
"""

import pandas as pd
import sys
import os
import math

# ================== 默认系数配置 ==================
# 普通商品毛利率分段阈值（小数形式）
LOW_MARGIN_THRESHOLD = 0.10      # 毛利率 <= 10% 为低毛利
HIGH_MARGIN_THRESHOLD = 0.65     # 毛利率 >= 65% 为高毛利

# 普通商品线下价格计算公式中的系数
LOW_MARGIN_RATIO = 0.8           # 低毛利：进货价 / LOW_MARGIN_RATIO
MID_MARGIN_RATIO = 0.6           # 中毛利：线上原价 * MID_MARGIN_RATIO
HIGH_MARGIN_RATIO = 0.35         # 高毛利：进货价 / HIGH_MARGIN_RATIO

# 固定毛利商品的分母（线下价格 = 进货价 / denominator）
FIXED_20_DENOM = 0.8             # 固定20%毛利对应分母
FIXED_15_DENOM = 0.85            # 固定15%毛利对应分母
# =================================================

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

def get_float(prompt, default):
    """交互式获取浮点数输入"""
    user_input = input(prompt).strip()
    if user_input == "":
        return default
    try:
        return float(user_input)
    except:
        print(f"输入无效，使用默认值 {default}")
        return default

def main():
    # 默认文件名
    input_file = "匹配结果_定价.csv"
    output_file = "匹配结果_线下定价.csv"
    file_fixed_20 = "线下毛利20%商品.csv"
    file_fixed_15 = "线下毛利15%商品.csv"

    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    # 检查主文件
    if not os.path.exists(input_file):
        print(f"错误：文件 {input_file} 不存在")
        return

    df = pd.read_csv(input_file, encoding='utf-8-sig')
    required_cols = ['内部条码', '进货价', '线上原价']
    for col in required_cols:
        if col not in df.columns:
            print(f"错误：输入文件缺少列 {col}")
            return

    df['进货价'] = pd.to_numeric(df['进货价'], errors='coerce')
    df['线上原价'] = pd.to_numeric(df['线上原价'], errors='coerce')
    df['内部条码'] = df['内部条码'].astype(str).str.strip()

    # 交互式设置系数
    print("\n=== 线下价格计算系数设置（直接回车使用默认值）===")
    low_thresh = get_float(f"低毛利阈值 (默认 {LOW_MARGIN_THRESHOLD}): ", LOW_MARGIN_THRESHOLD)
    high_thresh = get_float(f"高毛利阈值 (默认 {HIGH_MARGIN_THRESHOLD}): ", HIGH_MARGIN_THRESHOLD)
    low_ratio = get_float(f"低毛利线下价格分母 (进货价 / ? ，默认 {LOW_MARGIN_RATIO}): ", LOW_MARGIN_RATIO)
    mid_ratio = get_float(f"中毛利线下价格系数 (线上原价 * ? ，默认 {MID_MARGIN_RATIO}): ", MID_MARGIN_RATIO)
    high_ratio = get_float(f"高毛利线下价格分母 (进货价 / ? ，默认 {HIGH_MARGIN_RATIO}): ", HIGH_MARGIN_RATIO)
    fixed20 = get_float(f"固定20%毛利分母 (默认 {FIXED_20_DENOM}): ", FIXED_20_DENOM)
    fixed15 = get_float(f"固定15%毛利分母 (默认 {FIXED_15_DENOM}): ", FIXED_15_DENOM)

    # 读取固定毛利商品条码
    fixed_20_barcodes = set()
    fixed_15_barcodes = set()
    if os.path.exists(file_fixed_20):
        df20 = pd.read_csv(file_fixed_20, encoding='utf-8-sig')
        if '条码' in df20.columns:
            fixed_20_barcodes = set(df20['条码'].astype(str).str.strip())
            print(f"已加载固定20%毛利商品：{len(fixed_20_barcodes)} 条")
        else:
            print(f"警告：{file_fixed_20} 缺少 '条码' 列，忽略该文件")
    else:
        print(f"警告：未找到 {file_fixed_20}，忽略固定20%毛利商品")

    if os.path.exists(file_fixed_15):
        df15 = pd.read_csv(file_fixed_15, encoding='utf-8-sig')
        if '条码' in df15.columns:
            fixed_15_barcodes = set(df15['条码'].astype(str).str.strip())
            print(f"已加载固定15%毛利商品：{len(fixed_15_barcodes)} 条")
        else:
            print(f"警告：{file_fixed_15} 缺少 '条码' 列，忽略该文件")
    else:
        print(f"警告：未找到 {file_fixed_15}，忽略固定15%毛利商品")

    def calculate_offline_price(row):
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

    # 应用计算
    result = df.apply(calculate_offline_price, axis=1)
    df['线下价格_raw'] = result.apply(lambda x: x[0])
    df['定价类型'] = result.apply(lambda x: x[1])
    # 使用区间进位规则
    df['线下价格'] = df['线下价格_raw'].apply(round_price_by_interval)
    df.drop('线下价格_raw', axis=1, inplace=True)

    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"线下价格计算完成，结果保存至：{output_file}")
    print(f"有效线下价格商品数：{df['线下价格'].notna().sum()}")
    print("定价类型分布：")
    print(df['定价类型'].value_counts(dropna=False))

if __name__ == '__main__':
    main()