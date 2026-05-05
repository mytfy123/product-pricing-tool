#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
商品分类脚本（支持自定义占比阈值）
模式1（推荐）：基于非零销量的商品数量占比分类，零销量归为C类
              A类占比、B类占比可自定义（例如 A=0.2, B=0.6）
模式2：基于销量数值阈值分类（手动指定边界）
"""

import pandas as pd
import sys
import os

# ================== 配置参数 ==================
CLASSIFY_MODE = "nonzero_quantile"  # 可选: "nonzero_quantile" 或 "fixed_threshold"

# 模式1的占比配置（仅在 CLASSIFY_MODE="nonzero_quantile" 时生效）
A_RATIO = 0.2  # A类商品数量占比（前20%）
B_RATIO = 0.6  # B类商品数量占比（前20%~60%）

# 模式2的阈值配置（仅在 CLASSIFY_MODE="fixed_threshold" 时生效）
THRESHOLD_A = 100  # 销量 >= 100 为 A 类
THRESHOLD_B = 50  # 50 <= 销量 < 100 为 B 类；销量 < 50 为 C 类


# =============================================

def get_ratios_from_user():
    """交互式获取 A_RATIO 和 B_RATIO，返回 (a_ratio, b_ratio)"""
    print("\n请输入分类占比（直接回车使用当前配置）:")
    a_input = input(f"A类商品数量占比 (0~1, 默认 {A_RATIO}): ").strip()
    b_input = input(f"B类商品数量占比 (0~1, 默认 {B_RATIO}): ").strip()
    a = float(a_input) if a_input else A_RATIO
    b = float(b_input) if b_input else B_RATIO
    if not (0 < a < b <= 1):
        print("警告：占比必须满足 0 < A_RATIO < B_RATIO <= 1，将使用默认值")
        return A_RATIO, B_RATIO
    return a, b


def classify_by_nonzero_quantile(df, sales_col='总销量', a_ratio=0.2, b_ratio=0.6):
    """
    模式1：对销量 > 0 的商品按数量占比分 ABC
    A类：销量前 a_ratio 的商品（按销量降序，相同销量捆绑）
    B类：销量在 a_ratio ~ b_ratio 之间的商品
    C类：剩余商品（包括零销量或负销量）
    """
    df_result = df.copy()
    positive_mask = (df[sales_col] > 0) & (df[sales_col].notna())

    if positive_mask.sum() == 0:
        print("警告：没有销量大于0的商品，所有商品归为C类")
        df_result['商品分类'] = 'C'
        return df_result

    # 对正销量商品按销量值分组并降序排序
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
    print("分类数量分布：")
    print(df_result['商品分类'].value_counts())
    return df_result


def classify_by_fixed_threshold(df, sales_col='总销量', a_thresh=100, b_thresh=50):
    """模式2：按固定销量数值划分"""
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
    print("分类数量分布：")
    print(df_result['商品分类'].value_counts())
    return df_result


def main():
    input_file = "匹配结果（全部）3.csv"
    output_file = "匹配结果_分类.csv"
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    if not os.path.exists(input_file):
        print(f"错误：文件 {input_file} 不存在")
        return

    df = pd.read_csv(input_file, encoding='utf-8-sig')
    if '总销量' not in df.columns:
        print("错误：文件中没有 '总销量' 列")
        return

    df['总销量'] = pd.to_numeric(df['总销量'], errors='coerce')

    # 选择模式
    print(f"当前分类模式：{CLASSIFY_MODE}")
    if CLASSIFY_MODE == "nonzero_quantile":
        # 询问是否自定义占比
        use_default = input(f"是否使用默认占比 (A={A_RATIO}, B={B_RATIO})？(y/n，默认 y): ").strip().lower()
        if use_default == 'n':
            a_ratio, b_ratio = get_ratios_from_user()
        else:
            a_ratio, b_ratio = A_RATIO, B_RATIO
        result = classify_by_nonzero_quantile(df, '总销量', a_ratio, b_ratio)
    elif CLASSIFY_MODE == "fixed_threshold":
        result = classify_by_fixed_threshold(df, '总销量', THRESHOLD_A, THRESHOLD_B)
    else:
        print("未知模式，请设置 CLASSIFY_MODE 为 'nonzero_quantile' 或 'fixed_threshold'")
        return

    result.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"分类结果已保存至：{output_file}")


if __name__ == '__main__':
    main()