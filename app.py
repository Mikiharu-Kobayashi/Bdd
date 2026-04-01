# -*- coding: utf-8 -*-
import streamlit as st
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import json
import re
import requests
from docx import Document
import io
import time

# --- ページ設定 ---
st.set_page_config(page_title="Quick BDD Analyzer", layout="wide")
st.title("Quick BDD（単一事業向け - FMP API版）")

# --- サイドバー：APIキー設定 ---
with st.sidebar:
    st.header("Settings")
    
    st.markdown("### 1. Gemini API Key")
    api_key = st.text_input("Enter Gemini API Key", type="password")
    
    if api_key:
        genai.configure(api_key=api_key)
        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            selected_model = next((m for m in available_models if 'flash' in m), available_models[0])
            st.success(f"Model Active: {selected_model}")
        except Exception:
            selected_model = "gemini-1.5-flash"
            st.warning(f"Using default model: {selected_model}")
    else:
        st.info("APIキーを入力すると分析が開始できます")

    st.markdown("### 2. FMP API Key")
    st.markdown("[FMP公式サイト](https://site.financialmodelingprep.com/developer/docs/dashboard)から取得した無料APIキーを入力してください")
    fmp_api_key = st.text_input("Enter FMP API Key", type="password")

# --- Word生成用の関数 ---
def create_word(target, description, report_text):
    doc = Document()
    doc.add_heading('Strategic BDD Report', 0)
    doc.add_heading(f'Target Analysis: {target}', level=1)
    doc.add_paragraph(f"Description: {description}")
    doc.add_heading('Analysis Results', level=1)
    doc.add_paragraph(report_text)
    
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# --- FMP APIから財務データを取得する関数（エラー検知強化版） ---
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fmp_financials(ticker_code, fmp_key):
    # FMPの制限対策（念のため少し待機）
    time.sleep(0.5)
    
    # 4桁の数字のみの場合は、日本株指定のために.Tを付与する
    raw_ticker = str(ticker_code).strip()
    if re.match(r'^\d{4}$', raw_ticker):
        ticker = f"{raw_ticker}.T"
    else:
        ticker = raw_ticker

    base_url = "https://financialmodelingprep.com/api/v3"
    params = {"apikey": fmp_key}
    
    try:
        # 1. クォート情報（時価総額など）
        quote_res = requests.get(f"{base_url}/quote/{ticker}", params=params).json()
