#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
小程序价格计算脚本（可自定义系数 + 区间进位）
- 输入：第4个脚本输出的文件（如匹配结果_线下定价.csv），需包含列：
  线下价格, 线上活动价, 进货价
- 输出：原文件增加“小程序价格”列，价格按区间进位（.2/.5/.9）

计算逻辑（系数可交互输入）：
  基准 = 线下价格 * 基准倍数
  如果 基准 > 线上活动价:
      若 线上活动价 > 进货价 / 分母阈值, 则 小程序价 = 线上活动价
      否则 小程序价 = 进货价 / 分母阈值
  否则:
      小程序价 = 基准
  如果线上活动价无效（缺失、非数值、“#N/A”等），则 小程序价 = 基准
"""

import pandas as pd
import sys
import os
import math

def round_price_by_interval(value):
    """
    将价格按小数部分区间映射（与前面脚本一致）：
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
    except ValueError:
        print(f"输入无效，使用默认值 {default}")
        return default

def is_valid_activity_price(val):
    """判断线上活动价是否有效（非空、不是#N/A、能转为数字）"""
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
    """
    base_multiplier: 线下价格 × 倍数（默认1.1）
    denom: 进货价 / denom 的阈值（默认0.8）
    """
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

def main():
    input_file = "匹配结果_线下定价.csv"
    output_file = "匹配结果_小程序定价.csv"

    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    if not os.path.exists(input_file):
        print(f"错误：文件 {input_file} 不存在")
        return

    df = pd.read_csv(input_file, encoding='utf-8-sig')
    required_cols = ['线下价格', '线上活动价', '进货价']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"错误：输入文件缺少列 {missing}")
        print("实际列：", df.columns.tolist())
        return

    # 转换数值列
    df['线下价格'] = pd.to_numeric(df['线下价格'], errors='coerce')
    df['进货价'] = pd.to_numeric(df['进货价'], errors='coerce')
    # 线上活动价保留原样，由函数判断

    # ========== 交互式设置系数 ==========
    print("\n=== 小程序价格计算系数设置 ===")
    base_multiplier = get_float("线下价格 × 倍数（基准），默认 1.1: ", 1.1)
    denom = get_float("进货价 / 分母阈值（比较线上活动价），默认 0.8: ", 0.8)

    # 计算原始小程序价格
    df['小程序价格_raw'] = df.apply(
        lambda row: calculate_miniprogram_price(row, base_multiplier, denom), axis=1
    )
    # 应用区间进位规则
    df['小程序价格'] = df['小程序价格_raw'].apply(round_price_by_interval)

    df.drop('小程序价格_raw', axis=1, inplace=True)

    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"\n小程序价格计算完成，结果保存至：{output_file}")
    print(f"有效小程序价格商品数：{df['小程序价格'].notna().sum()}")
    print("小程序价格统计：")
    print(df['小程序价格'].describe())

if __name__ == '__main__':
    main()