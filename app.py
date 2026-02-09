# -*- coding: utf-8 -*-
import streamlit as st
import google.generativeai as genai
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import json
import re
from fpdf import FPDF
import base64
from docx import Document
import io


# --- ページ設定 ---
st.set_page_config(page_title="Quick BDD Analyzer", layout="wide")
st.title("Quick BDD（単一事業）")

# --- サイドバー：APIキー設定 ---
with st.sidebar:
    st.header("Settings")
    
    # APIキーの取得方法を案内
    st.markdown("### 🔑 API Key Setup")
    st.markdown("""
    1. [Google AI Studio](https://aistudio.google.com/app/apikey) にアクセス
    2. 'Create API key' をクリック
    3. キーをコピーして以下に貼り付けてください
    """)
    
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

# --- 1. 競合特定フェーズ ---
with st.form(key='search_form'):
    target_name = st.text_input("分析したい企業の名前を入力してください", "")
    submit_button = st.form_submit_button(label='分析開始')

# ボタンが押されるか、Enterが叩かれた時の処理
if submit_button:
    if not api_key:
        st.error("左上の矢印 >> からサイドバーを開き、APIキーを入力してください")
    elif target_name:
        model = genai.GenerativeModel(selected_model)
        with st.spinner(f"🔍 {target_name} の市場構造と競合候補を調査中..."):
            comp_prompt = f"""
            「{target_name}」のBDDを行います。以下をJSON形式のみで出力してください。
            {{
              'description': '対象企業の概要',
              'competitors': [
                {{'name': '企業名', 'ticker': '銘柄コード.T', 'reason': '競合となりうる理由(30文字以内)'}}
              ]
            }}
            """
            try:
                res = model.generate_content(comp_prompt)
                data = json.loads(re.search(r'\{.*\}', res.text, re.DOTALL).group())
                
                # セッションに保存
                st.session_state.target_desc = data['description']
                st.session_state.all_competitors = data['competitors']
                st.session_state.step = 2
                # フォーム送信後は自動で再描画されるため、入力内容が確定します
            except Exception as e:
                st.error(f"AIによる調査でエラーが発生しました: {e}")
# --- 2. 競合選択 & 財務分析フェーズ ---
if "step" in st.session_state and st.session_state.step >= 2:
    st.subheader(f"対象企業の概要: {target_name}")
    st.info(st.session_state.target_desc)

    st.subheader("分析対象の選択")
    st.write("AIが特定した競合候補です。分析に含める企業を選択してください。")

    # 選択用データの準備
    comp_list = st.session_state.all_competitors
    selected_tickers = []  # ここに統一
    
    # 競合理由を表示しながらチェックボックスを並べる
    for comp in comp_list:
        # チェックボックスを表示し、ONなら ticker をリストに追加
        if st.checkbox(f"**{comp['name']}** ({comp['ticker']}) — {comp['reason']}", value=True, key=f"check_{comp['ticker']}"):
            selected_tickers.append(comp['ticker'])

    # 分析実行ボタン
    if st.button("選択した企業で詳細分析・レポート生成", key="exec_analysis"):
        # 選択された ticker に基づいてフィルタリング
        final_competitors = [c for c in comp_list if c['ticker'] in selected_tickers]
        
        if not final_competitors:
            st.error("少なくとも1社は選択してください。")
        else:
            model = genai.GenerativeModel(selected_model)
            
            with st.spinner("📡 財務データを抽出中..."):
                summary_results = []
                for comp in final_competitors:
                    try:
                        stock = yf.Ticker(comp['ticker'])
                        info = stock.info
                        summary_results.append({
                            "企業名": comp['name'],
                            "時価総額(億)": round(info.get('marketCap', 0) / 1e8, 1),
                            "売上高(億)": round(info.get('totalRevenue', 0) / 1e8, 1),
                            "営業利益率(%)": round(info.get('operatingMargins', 0) * 100, 1),
                            "ROE(%)": round(info.get('returnOnEquity', 0) * 100, 1)
                        })
                    except: continue
                
                if not summary_results:
                    st.error("財務データの取得に失敗しました。")
                else:
                    df = pd.DataFrame(summary_results)
                    st.subheader("競合の主要財務数値")
                    st.dataframe(df.style.format(precision=1), use_container_width=True)
            
                    # 3. ビジュアル化
                    st.subheader("競合ポジショニングマップ")
                    plot_df = df.copy()
                    plot_df["表示サイズ"] = plot_df["時価総額(億)"].apply(lambda x: 0.1 if x <= 0 else x)
            
                    fig = px.scatter(
                        plot_df, 
                        x="営業利益率(%)", 
                        y="ROE(%)", 
                        size="表示サイズ", 
                        color="企業名", 
                        text="企業名", 
                        template="plotly_white",
                        labels={"表示サイズ": "時価総額(億)"}
                    )
                    fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color="white"))
                    st.plotly_chart(fig, use_container_width=True)
               
                    # 4. BDDレポート生成
                    with st.spinner("📝 戦略コンサル視点での提言を生成中..."):
                        table_str = df.to_markdown()
                        report_prompt = f"""
                        ゴールドマンサックス（IB）のシニアアナリストになったつもりで
                        以下の最新財務比較データに基づき、「{target_name}」に関連する市場環境と競合他社の分析レポートを作成してください。
                        アウトプットはレポート名から簡潔に始めてください。ゴールドマンサックスやシニアアナリストなどは表示しないでください。
                        【比較対象データ】
                        {table_str}
                    
                        【読みやすさの厳格ルール】
                        1. 構造化：## で章を、### で節を区切り、情報の階層を明確にすること。
                        2. 視覚化：重要な数値や結論は **太字** で強調し、3つ以上の項目は必ず箇条書きにすること。
                        3. 簡潔性：1文を短くし、専門用語には平易な注釈を添えること。
                        4. 要約：各章の冒頭に「> 結論：(一行要約)」を記述すること。
                        【レポート構成】
                        1. エグゼクティブ・サマリー：
                           業界全体のファンダメンタルズ/競合優位性/ {target_name}の現状および強みを踏まえた成長戦略。
                           1-1.事業戦略
                           1-2.人事戦略
                           1-3.財務戦略
                        2. 市場構造と成長ドライバー:
                           2-1.業界のKFSと現在および将来のマクロ環境が与えるインパクト
                           2-2.足元成長を後押ししているトレンド/成長率の高いセグメント（顧客層/価格帯など）
                           2-3.マルチプル（PER等）の観点から見た、業界のバリュエーション水準。
                        3. 稼ぐ力と資本効率の定量的比較：
                           3-1.営業利益率とROEの相関から見る、競合各社の「参入障壁」と「経営効率」の差を生む構造的要因（コスト構造、アセットライトモデル等）の解剖
                           3-2.競合各社の財務数値から透けて見える「各社の勝ちパターン/強み」の特定
                        4. {target_name}（非上場）への戦略的提言：
                           4-1.マーケットと競合、  {target_name}の公開データから取りうる戦略オプションの洗い出し
                           4-2.数年で2-3倍の収益を目指す目線での仮評価
                           　　現状のバリュエーション水準に基づき、どのようなレバー（出店加速、単価アップ等）を引けば企業価値が最大化するか。
                        5. 事業推進上の致命的リスク（Red Flags）:
                           BDDの観点から、将来の成長を阻害する可能性のある構造的リスク。
                    
                        プロフェッショナルな論調で、具体的数値に基づいた示唆を出してください。
                        """
                                
                        report = model.generate_content(report_prompt)
                        report_content = report.text
                    
                        st.markdown("---")
                        st.markdown("## 対象企業・業界に対する初期仮説")
                        st.markdown(report_content)
                    
                        # --- 出力セクション ---
                        st.markdown("---")
                          
                        try:
                            word_data = create_word(target_name, data['description'], report_content)
                            st.download_button(
                                label="Word形式で保存",
                                data=word_data,
                                file_name=f"Quick BDD_Report_{target_name}.docx",
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key="word_download"
                            )
                        except Exception as e:
                            st.error(f"Word生成に失敗しました: {e}")
