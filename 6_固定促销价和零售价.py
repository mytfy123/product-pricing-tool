#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
价格替换脚本（促销价 / 零售价）精简版
- 输入：第5个脚本输出的文件（如匹配结果_小程序定价.csv）
- 额外输入：促销价.csv（条码,固定促销价）、零售价.csv（条码,建议零售价）
- 输出：替换线下价格、小程序价格、定价类型，价格向上取整到0.1
"""

import pandas as pd
import sys
import os
import math
import re

def ceil_to_one_decimal(value):
    if pd.isna(value):
        return value
    try:
        return math.ceil(value * 10) / 10
    except:
        return value

def clean_barcode(barcode):
    """清洗条码：去空格、去除末尾.0、科学计数法转整数"""
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

def main():
    input_file = "匹配结果_小程序定价.csv"
    output_file = "匹配结果_最终定价2.csv"
    promo_file = "固定促销价.csv"
    retail_file = "固定零售价.csv"

    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    if not os.path.exists(input_file):
        print(f"错误：文件 {input_file} 不存在")
        return

    # 读取主文件
    df = pd.read_csv(input_file, encoding='utf-8-sig')
    required = ['内部条码', '线下价格', '小程序价格', '定价类型']
    for col in required:
        if col not in df.columns:
            print(f"错误：主文件缺少列 {col}")
            return

    df['内部条码'] = df['内部条码'].apply(clean_barcode)

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
    for idx, row in df.iterrows():
        bc = row['内部条码']
        if not bc:
            continue
        if bc in promo:
            price = promo[bc]
            df.at[idx, '线下价格'] = ceil_to_one_decimal(price)
            df.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
            df.at[idx, '定价类型'] = "固定促销价"
            replaced += 1
        elif bc in retail:
            price = retail[bc]
            df.at[idx, '线下价格'] = ceil_to_one_decimal(price)
            df.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
            df.at[idx, '定价类型'] = "建议零售价"
            replaced += 1

    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"替换完成，共替换 {replaced} 条商品，结果保存至 {output_file}")

if __name__ == '__main__':
    main()