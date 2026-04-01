# -*- coding: utf-8 -*-
import streamlit as st
import google.generativeai as genai
import yfinance as yf
import pandas as pd
import plotly.express as px
import json
import re
from docx import Document
import io
import time

# --- ページ設定 ---
st.set_page_config(page_title="Quick BDD Analyzer", layout="wide")
st.title("Quick BDD（単一事業向け - yfinance安定版）")

# --- サイドバー：APIキー設定 ---
with st.sidebar:
    st.header("Settings")
    
    st.markdown("### Gemini API Key")
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

# --- 🌟 yfinanceデータ取得関数（キャッシュ＆Sleep強化版） ---
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_yf_financials(ticker_code):
    # 【重要】Yahoo FinanceからのIPブロックを防ぐための待機時間
    time.sleep(2)
    
    raw_ticker = str(ticker_code).strip()
    if re.match(r'^\d{4}$', raw_ticker):
        ticker = f"{raw_ticker}.T"
    else:
        ticker = raw_ticker

    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # yfinanceは企業が存在しなくても空の辞書を返すことがあるためチェック
        if not info or len(info) < 5:
            raise ValueError(f"Yahoo Financeに {ticker} のデータが存在しません。")

        val_market_cap = info.get('marketCap')
        val_op_margin = info.get('operatingMargins')
        val_roe = info.get('returnOnEquity')
        val_revenue = info.get('totalRevenue')

        summary = {
            "時価総額(億)": round(val_market_cap / 1e8, 1) if val_market_cap else 0,
            "売上高(億)": round(val_revenue / 1e8, 1) if val_revenue else 0,
            "営業利益率(%)": round(val_op_margin * 100, 1) if val_op_margin else 0,
            "ROE(%)": round(val_roe * 100, 1) if val_roe else 0
        }

        # 5年分の財務データ
        hist_pl = stock.financials 
        hist_bs = stock.balance_sheet 
        
        hist_text = "【損益計算書 (主要項目)】\n"
        if hist_pl is not None and not hist_pl.empty:
            # 存在する項目だけを安全に抽出
            pl_items = [i for i in ['Total Revenue', 'Operating Income', 'Net Income', 'Selling General Administrative'] if i in hist_pl.index]
            hist_text += hist_pl.loc[pl_items].to_string() + "\n"
        else:
            hist_text += "データなし\n"

        hist_text += "\n【貸借対照表 (主要項目)】\n"
        if hist_bs is not None and not hist_bs.empty:
            bs_items = [i for i in ['Total Assets', 'Stockholders Equity', 'Inventory', 'Accounts Receivable', 'Accounts Payable'] if i in hist_bs.index]
            hist_text += hist_bs.loc[bs_items].to_string() + "\n"
        else:
            hist_text += "データなし\n"

        return summary, hist_text

    except Exception as e:
        raise ValueError(f"取得エラー ({ticker}): {str(e)}")


# --- 1. 競合特定フェーズ ---
with st.form(key='search_form'):
    target_name = st.text_input("分析したい企業の名前を入力してください", "")
    submit_button = st.form_submit_button(label='分析開始')

if submit_button:
    if not api_key:
        st.error("左上の矢印 >> からサイドバーを開き、Gemini APIキーを入力してください")
    elif not target_name:
        st.warning("企業名を入力してください")
    else:
        model = genai.GenerativeModel(selected_model)
        with st.spinner(f"🔍 {target_name} を調査中..."):
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
                match = re.search(r'\{.*\}', res.text, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    st.session_state.target_desc = data['description']
                    st.session_state.all_competitors = data['competitors']
                    st.session_state.step = 2
                else:
                    st.error("AIからの応答を解析できませんでした。もう一度お試しください。")
            except Exception as e:
                st.error(f"AIによる調査でエラーが発生しました: {e}")

# --- 2. 競合選択 & 財務分析フェーズ ---
if "step" in st.session_state and st.session_state.step >= 2:
    st.subheader(f"対象企業の概要: {target_name}")
    st.info(st.session_state.target_desc)

    st.subheader("分析対象の選択")
    st.write("AIが特定した競合候補です。分析に含める企業を選択してください。")

    comp_list = st.session_state.all_competitors
    selected_tickers = []  
    
    for comp in comp_list:
        if st.checkbox(f"**{comp['name']}** ({comp['ticker']}) — {comp['reason']}", value=True, key=f"check_{comp['ticker']}"):
            selected_tickers.append(comp['ticker'])

    if st.button("選択した企業で詳細分析・レポート生成", key="exec_analysis"):
        final_competitors = [c for c in comp_list if c['ticker'] in selected_tickers]
        
        if not final_competitors:
            st.error("少なくとも1社は選択してください。")
        else:
            model = genai.GenerativeModel(selected_model)
            with st.spinner("📡 Yahoo Financeから詳細財務データを抽出中（アクセス制限回避のため少し時間がかかります）..."):
                summary_results = []
                detailed_financials_for_ai = ""
                error_targets = []
                
                # プログレスバーを表示してユーザーの体感待ち時間を軽減
                progress_bar = st.progress(0)
                total_comps = len(final_competitors)

                for i, comp in enumerate(final_competitors):
                    try:
                        # yfinance関数呼び出し（キャッシュ＋2秒待機）
                        summary, hist_text = fetch_yf_financials(comp['ticker'])
                        
                        summary["企業名"] = comp['name']
                        summary_results.append(summary)

                        detailed_financials_for_ai += f"\n--- {comp['name']} ({comp['ticker']}) 過去5年分財務データ ---\n"
                        detailed_financials_for_ai += hist_text
                        detailed_financials_for_ai += "\n"

                    except Exception as e:
                        error_targets.append(str(e))
                    
                    # プログレスバーを更新
                    progress_bar.progress((i + 1) / total_comps)
                
                if error_targets:
                    st.warning("一部のデータ取得に制限がありました:\n\n" + "\n\n".join(error_targets))

                if not summary_results:
                    st.error("財務データの取得にすべて失敗しました。")
                else:
                    df = pd.DataFrame(summary_results)
                    df = df[["企業名", "時価総額(億)", "売上高(億)", "営業利益率(%)", "ROE(%)"]]
                    st.subheader("競合の主要財務数値（最新）")
                    st.dataframe(df.style.format(precision=1), use_container_width=True)
            
                    st.subheader("競合ポジショニングマップ（最新）")
                    plot_df = df.copy()
                    plot_df["表示サイズ"] = plot_df["時価総額(億)"].apply(lambda x: 0.1 if x <= 0 else x)
            
                    fig = px.scatter(
                        plot_df, x="営業利益率(%)", y="ROE(%)", size="表示サイズ", 
                        color="企業名", text="企業名", template="plotly_white",
                        labels={"表示サイズ": "時価総額(億)"}
                    )
                    fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color="white"))
                    st.plotly_chart(fig, use_container_width=True)
                    
                    with st.spinner("📝 市場分析およびバリューアップ仮説を生成中..."):
                        report_prompt = f"""
                        あなたは、トップティアの戦略コンサルティングファーム出身で、現在は大手PEファンドの投資委員（ICメンバー）です。
                        「{target_name}」のBDD（ビジネス・デューデリジェンス）において、特に「アップサイド・ポテンシャル」と「バリューアップ計画（VCP）」に焦点を当てた投資判断資料を作成してください。

                        与えられた財務データ（{detailed_financials_for_ai}）を起点に、一般的な市場解説ではなく、「この会社は、具体的にどのレバーを引けば企業価値（EV）が2倍、3倍になるか？」という視点で、ドライかつ論理的に、そして商品名やECサイト名称、組織名などの固有名詞を用いながら対象会社を具体的に記述してください。
                        アウトプットは章の名から簡潔に始めてください。
                        内容を深めるため、BDD/ファンドなどの単語を書いていますがOutputには含めず、対象企業の純粋な分析レポートとして様々な用途に対応できるようにしてください。投資判断に限定しないでください。
                        
                        【読みやすさの厳格ルール】
                        1. 構造化：## で章を、### で節を区切り、情報の階層を明確にすること。
                        2. 視覚化：重要な数値や結論は **太字** で強調し、3つ以上の項目は必ず箇条書きにすること。ただし、Word出力時にAIライクにならないよう *の多用は避ける
                        3. 簡潔性：1文を短くし、専門用語には平易な注釈を添えること。
                        4. 要約：各章の冒頭に「> 結論：(一行要約)」を記述すること。
                        
                        【レポート構成】
                        1. エグゼクティブ・サマリー：
                           業界全体のファンダメンタルズ/競合優位性/ {target_name}の現状および強みを踏まえた成長戦略。
                           1-1.事業戦略
                           1-2.人事戦略
                           1-3.財務戦略
                        2. 市場分析
                           2-1.現状
                               市場規模（TAM/SAM/SOMの推計）
                               市場の成長率
                               市場の利益率
                               競争の激化要因（価格競争か、機能競争か）
                           2-2.将来性
                               足元成長を後押ししているトレンド/成長率の高いセグメント（顧客層/価格帯など）の最新トピック
                               現在および将来のマクロ環境（PEST）が与えるインパクト      
                        3. 競合分析
                            3-1.各社の特徴
                                バリューチェーン
                                ターゲット顧客/ポジショニング
                                マーケティング戦略（product/price/place/promotion)
                            3-2.各社の財務分析
                                収益性の源泉とコスト構造の解剖
                                    限界利益率/オペレーティング・レバレッジ（売上増がどれだけ利益増に直結するか）
                                    利益の質（広告宣伝費や販促費の比率から、ブランド力による「指名買い」なのか「販促による押し込み」なのか）
                                    ユニットエコノミクス推論（財務数値から、各社の顧客獲得コストやLTVといった事業推進のカギとなる値の推定）
                                デュポン分析的視点
                                    ROEを「売上高純利益率 × 総資産回転率 × 財務レバレッジ」の要素に分解し、競合との差を特定
                                    差を生む構造的要因（コスト構造、アセットライトモデル等）の解剖
                                CCC (キャッシュ・コンバージョン・サイクル):
                                    売上債権・棚卸資産の回転期間から、各社の運転資本（Working Capital）の管理能力と、成長に伴う資金需要の強さを推測
                        4. {target_name}への戦略的提言
                           {target_name}の情報が少ない場合、公開情報から推計して記載してください。～と仮定します、などは都度書く必要はありません
                           4-1.マーケットと競合、  {target_name}の公開データから取りうる戦略オプションの洗い出し
                               トップラインの強化: 新規顧客開拓、価格決定権の行使（値上げ）、新商材展開の余地。
                               コストの最適化: 競合比較から見える、コスト削減の余地（DXによる販管費削減、サプライチェーン見直し）。
                           4-2.数年で2-3倍の収益を目指す目線での仮評価
                                現在の時価総額・マルチプルの高さが、どの指標（売上成長率か、それともROEか）に最も強く相関しているかを特定し
                                どのようなレバー（出店加速、単価アップ等）を引けば企業価値が最大化するか
                        5. 事業推進上の致命的リスク（Red Flags）:
                           最悪のシナリオ（法規制、強力な新規参入、技術的陳腐化）
                           それに対する具体的な緩和策（Mitigation Plan）
                    
                        プロフェッショナルな論調で、具体的数値に基づいた示唆を出してください。
                        """
                        
                        try:
                            report_response = model.generate_content(report_prompt)
                            report_content = report_response.text
                            
                            st.divider()
                            st.markdown(report_content)
                            
                            st.markdown("---")
                            try:
                                desc_text = st.session_state.get('target_desc', '概要なし')
                                word_data = create_word(target_name, desc_text, report_content)
                                
                                st.download_button(
                                    label="📝 Word形式で保存",
                                    data=word_data,
                                    file_name=f"Quick_BDD_Report_{target_name}.docx",
                                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                    key="word_download"
                                )
                            except Exception as word_err:
                                st.error(f"Word生成中にエラーが発生しました: {word_err}")
                    
                        except Exception as api_err:
                            st.error(f"レポート生成中にAIエラーが発生しました: {api_err}")
