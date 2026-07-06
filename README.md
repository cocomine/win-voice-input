# Win Voice Input

Win Voice Input 是一個 Windows 語音輸入工具，使用 Google Speech-to-Text 將語音轉成文字。程式會在聆聽期間顯示正在識別中的文字，等 Google 回傳 final 結果後，才把最終文字貼到目前正在使用的輸入框。

## 軟件特點

- 支援香港粵語，預設語言為 `yue-Hant-HK`。
- 使用 Windows 預設輸入裝置；亦可在 Settings 指定其他 microphone。
- 使用 `Ctrl+Alt+Space` 或 tray menu 開始 / 暫停語音輸入。
- 聆聽期間會顯示 overlay 狀態視窗，讓使用者看到目前正在識別的文字。
- 聆聽期間可按 `Enter` 貼上目前 preview，而不會停止聆聽；未聆聽時不會攔截正常 `Enter`。
- 預設會貼上 final 結果；如果 session 結束時仍未變成 final，但 preview 仍有文字，也會貼上該段 preview。可在 Settings 關閉 preview 收尾輸出。
- 使用 Windows clipboard + paste 方式輸出 final 文字，適合中文輸入。
- Tray icon 會顯示 Idle / Listening / Stopping 狀態。
- 開始聆聽和停止聆聽時會播放提示音。
- 如果一段時間沒有識別到文字，預設 5 秒後自動停止聆聽。
- Settings 介面可修改 Google credentials、microphone、語言、音效、overlay 位置、自動啟動等設定。
- 可設定登入 Windows 後自動啟動。
- 錯誤會以 Windows message box 顯示，避免背景程式靜默失敗。
- 診斷 log 會寫入 `%LOCALAPPDATA%\WinVoiceInput\logs\win-voice-input.log`。

## 第一次使用已編譯好的 exe

1. 解壓縮發佈版本，例如：

```text
WinVoiceInput\
  WinVoiceInput.exe
  config.json
  ...
```

2. 準備 Google service account key JSON 檔案。

3. 執行 `WinVoiceInput.exe`。

4. 如果尚未設定 Google credentials，程式會顯示 setup error。選擇 `Yes` 打開 Settings。

5. 在 Settings 選擇 Google service account `.json` 檔案，確認其他設定後按 Save。

6. 儲存成功後，Win Voice Input 會自動重新啟動。

7. 重新啟動後，tray icon 會出現在 Windows 右下角通知區域。某些 Windows 設定會把新圖示收在 `^` 隱藏圖示區。

## 基本使用方法

1. 打開 Notepad、Word、瀏覽器輸入框，或任何你想輸入文字的地方。

2. 按 `Ctrl+Alt+Space`，或者在 tray menu 選擇 Start listening。

3. 開始說話。聆聽期間 overlay 會顯示目前識別中的文字。

4. 當 Google 回傳 final 結果後，程式會自動把文字貼到目前輸入框。如果 Google 很久仍未回傳 final，可在聆聽中按 `Enter` 立即貼上目前 preview，聆聽會繼續；如果稍後 Google 回傳完全相同的 final，程式會避免重複貼上。

5. 再按一次 `Ctrl+Alt+Space`，或者在 tray menu 選擇 Pause listening，即可停止目前聆聽 session。

暫停時，程式不會錄音，也不會把音訊送到 Google。

## Tray Menu

在 Windows 右下角 tray icon 按右鍵，可以使用以下功能：

- `Start listening`：開始語音輸入。
- `Pause listening`：停止目前聆聽 session。
- `Settings...`：打開設定介面。
- `Open logs folder`：打開 log folder。
- `Open config folder`：打開 `config.json` 所在 folder。
- `Exit`：關閉 Win Voice Input。

## Settings 說明

Settings 會修改 `config.json`。成功儲存後，如果 Settings 是從 tray app 或 startup setup prompt 打開，Win Voice Input 會自動重新啟動並載入新設定。

常用設定：

- `Google credentials JSON`：Google service account key JSON 檔案。
- `Input device`：選擇 microphone；預設是 Windows default input device。
- `Language`：Google STT 語言代碼，預設 `yue-Hant-HK`。
- `Sample rate`：microphone sample rate，預設 `16000`。
- `Paste final transcripts`：是否把 final 結果貼到目前輸入框。
- `Paste preview when session ends without final`：session 結束而 Google 尚未回傳 final 時，是否把最新 preview 文字貼到目前輸入框。
- `Append space after final text`：是否在 final 文字後自動加空格。
- `Duplicate protection seconds`：短時間內避免重複貼上同一 final 結果。
- `Idle auto-stop seconds`：沒有識別到文字後自動停止聆聽的秒數；設為 `0` 可停用。
- `Play start/end sounds`：開始 / 停止聆聽時是否播放音效。
- `Show listening indicator`：是否顯示 overlay 狀態視窗。
- `Indicator position`：overlay 顯示位置。
- `Start with Windows`：登入 Windows 後自動啟動。

## Overlay 狀態視窗

聆聽時，畫面上會顯示一個小型 overlay 狀態視窗。它的用途是讓使用者知道語音正在被識別，而且可以看到 Google 目前回傳的中途文字。

中途文字不會在聆聽期間即時輸入到目前輸入框。原因是 Google Speech-to-Text 的 interim result 會不停修正，如果直接寫入輸入框，容易造成重複文字、刪除錯位或輸入法相容問題。Win Voice Input 會優先貼上 final 結果；如果你想手動提交目前看到的 preview，可在聆聽中按 `Enter`。`Enter` 只會在 Listening 狀態被攔截，平時仍保留原本功能。如果 session 結束且沒有 final 可用，預設會把最後一段 preview 當作收尾文字貼上。關閉 `Paste preview when session ends without final` 後，只會停用 session 結束時的自動 preview 收尾；聆聽中的 `Enter` 仍可手動提交 preview。

## 自動停止

預設情況下，如果 5 秒內沒有任何已識別文字，程式會自動停止目前聆聽 session 並回到 Idle。這可以避免使用者忘記停止聆聽時，程式一直保持 microphone 和 Google stream。

你可以在 Settings 修改 `Idle auto-stop seconds`。

## Log 與錯誤

Log 位置：

```text
%LOCALAPPDATA%\WinVoiceInput\logs\win-voice-input.log
```

如果程式無法啟動、credentials 無效、hotkey 註冊失敗、microphone 或 Google STT 發生錯誤，程式會盡量用 Windows message box 顯示錯誤。需要排查時，可以把 log 檔案提供給開發者。

## 注意事項

- 使用前必須有有效的 Google service account key JSON。
- Final 文字會透過 Windows clipboard 貼上，所以 clipboard 內容會被最新 final 結果取代。
- 如果 microphone 在 `16000` Hz 無法使用，可以在 Settings 嘗試改成 `48000`。
- 某些 Windows 應用程式可能會限制 paste 或 clipboard 行為；這種情況通常會在 log 中留下錯誤。
- Google streaming session 本身有時間限制，建議以短句或一段一段方式使用。

## 開發文檔

開發者 setup、source run、build、架構圖、UML、流程圖、sequence diagram 和每個 class 的職責，請查看：

[docs/TECHNICAL_DOCUMENTATION.md](docs/TECHNICAL_DOCUMENTATION.md)
