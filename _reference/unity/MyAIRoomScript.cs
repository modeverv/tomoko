using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MyScripts.miniScripts;
using TMPro;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;
using UnityEngine.Serialization;
using UnityEngine.UI;
using VRM;
using SceneManager = WBTransition.SceneManager;
#if UNITY_WEBGL
using Microphone = FrostweepGames.MicrophonePro.Microphone;
#endif


namespace MyScripts.byScenes
{

    public class MyAIRoomScript : MonoBehaviour
    {
        private AudioClip voiceClip;
        private const int SampleRate = 44100;
        private const int MinimalResponseLen = 5;
        public AudioSource se;

        public MySharedData myShared;
        public MyData myData;
        public MyCommonUtil common;
        public GameObject character;
        public VRMBlendShapeProxy blendShapeProxy;

        public GameObject goRecSign;
        public GameObject goComSign;
        public GameObject goAISign;
        [FormerlySerializedAs("CHAT_LIST")] public GameObject chatList;
        private Scrollbar _verticalScrollbar;
        private RectTransform volSign;

        public bool isRecording;
        public bool isCommunicating;
        public bool isAITalking;
        public float threshold = 0.01f;
        public AudioSource aiVoice;
        [FormerlySerializedAs("userVoice")] public AudioSource audioSource;
        public string deviceName;
        public float volume = 0f;
        public float gaman = 1.5f;

        // about recording
        private readonly List<float> _audioData = new();
        private int _lastSample;

        // times
        private bool pauseTimer = true;
        private long _minute = 100;
        private float _oldSeconds;
        private float _seconds = 40;
        private bool _started;
        private TMP_Text _time;
        private float _totalTime;
        private float _timeWhenVolumeBelowThreshold;

        public string DUMMY_ROOM_ID = "14af87f8-8c2a-489b-8b0e-0ba35ad9a867-2";

        /// <summary>
        ///     initialize room
        /// </summary>
        public void Awake()
        {
            if (GameObject.Find("GAMEBGM"))
            {
                GameObject.Find("GAMEBGM").GetComponent<AudioSource>().enabled = false;
            }
            NativeLeakDetection.Mode = NativeLeakDetectionMode.EnabledWithStackTrace;

            // get shared data
            myShared = GameObject.Find("gameData").GetComponent<MySharedData>();
            myData = GameObject.Find("gameData").GetComponent<MyData>();
            common = GameObject.Find("gameData").GetComponent<MyCommonUtil>();

            character = GameObject.Find("character").gameObject;

            goRecSign = GameObject.Find("Canvas/RecSign").gameObject;
            goComSign = GameObject.Find("Canvas/ComSign").gameObject;
            goAISign = GameObject.Find("Canvas/AISign").gameObject;
            goRecSign.SetActive(false);
            goComSign.SetActive(false);
            goAISign.SetActive(false);
            chatList = GameObject.Find("Canvas/Chat/Log/Viewport/Content").gameObject;
            _verticalScrollbar = GameObject.Find("Canvas/Chat/Log/Scrollbar Vertical").GetComponent<Scrollbar>();
            volSign = GameObject.Find("Canvas/vol/vv").GetComponent<RectTransform>();

            blendShapeProxy = character.GetComponent<VRMBlendShapeProxy>();

            se = GameObject.Find("SE").GetComponent<AudioSource>();
            aiVoice = GameObject.Find("AIVoice").GetComponent<AudioSource>();
            audioSource = GameObject.Find("UserVoice").GetComponent<AudioSource>();

            try
            {
                if (!character.GetComponent<Blinker>())
                    character.AddComponent<Blinker>();
            }
            catch (Exception e)
            {
                MyCommonUtil.de(e);
            }

            Application.wantsToQuit += WantsToQuit;

            // set room info
            SetRoomInfo();
        }



        // Start is called before the first frame update
        private void Start()
        {
            _minute = (long)(myShared.durationSec / 60.0);
            _seconds = myShared.durationSec - 60 * _minute;
            var canvas = GameObject.Find("Canvas");
            _time = canvas.transform.Find("ControlPanel").transform.Find("time").GetComponent<TMP_Text>();
            _time.text = "";
            _totalTime = _minute * 60 + _seconds;
            _oldSeconds = 0f;
            _started = true;
#if UNITY_EDITOR
            if (myShared.roomid == "")
            {
                var roomid = DUMMY_ROOM_ID;
                common.GetReq("/api/ai_room/join/" + roomid + "/1" + "?nocache=" + Time.time).ContinueWith(t =>
                {
                    if (t.IsFaulted)
                        foreach (var innerException in t.Exception.InnerExceptions)
                            MyCommonUtil.de(innerException.Message);
                    else
                        MyCommonUtil.de(t.Result);
                });
            }
#endif
            Invoke(nameof(StartVoiceDetector), 0.1f);
            OnGainValueChange(1f);
        }

        // Update is called once per frame
        [Obsolete("Obsolete")]
        private void Update()
        {
            HandleRecord();
            HandleDisplay();
            UpdateIndicator();
        }

        private void UpdateIndicator()
        {
            var clampedValue = Mathf.Clamp(volume, 0f, 0.04f); // clampedValueは0.04に
            // 0 - 0.04 を 0 - 200にスケール
            var originalRange = 0.04f - 0f;
            var newValue = ((clampedValue - 0f) * (200f - 0f) / originalRange) + 0f;

            var y = -100 + (newValue / 2);
            var position = volSign.localPosition;
            position.y = y;
            volSign.localPosition = position;

            var size = volSign.sizeDelta;
            size.y = newValue;
            volSign.sizeDelta = size;
        }

        public void OnGainValueChange(float input)
        {
            // 0.04から0.001の範囲にスケールします。
            var output = 0.001f + input * (0.04f - 0.001f);
            threshold = output;
        }

        private void LateUpdate()
        {
            UpdateTime();
        }

        private void UpdateTime()
        {
            //　一旦トータルの制限時間を計測；
            _totalTime = _minute * 60 + _seconds;
            if (!pauseTimer)
            {
                if (!myShared.isHostJoin) _totalTime -= Time.deltaTime;
                if (myShared.doubleJoin) _totalTime -= Time.deltaTime;
            }
            //　再設定
            _minute = (int)_totalTime / 60;
            _seconds = _totalTime - _minute * 60;
            //　タイマー表示用UIテキストに時間を表示する
            if ((int)_seconds != (int)_oldSeconds)
            {
                _time.text = _minute.ToString("00") + ":" + ((int)_seconds).ToString("00");
            }

            _oldSeconds = _seconds;
            //　制限時間以下になったらコンソールに『制限時間終了』という文字列を表示する
            if (_totalTime <= 0f) _time.text = "END";
            if (_totalTime <= -0.25f)
            {
                if (_started)
                {
                    _started = false;
                    Close();
                }
            }
        }

        private void SetRoomInfo()
        {
            var canvas = GameObject.Find("Canvas");
            canvas.transform.Find("roomname").GetComponent<TMP_Text>().text = myShared.roomname;
        }

        private bool WantsToQuit()
        {
            LeaveRoom(true);
            return true;
        }

        public void CloseGuidance()
        {
            pauseTimer = false;
            var canvas = GameObject.Find("Canvas");
            canvas.transform.Find("Panel").gameObject.SetActive(false);
        }

        private void LeaveRoom(bool isQuit = false)
        {
            var url = "/api/ai_room/leave/" + myShared.roomId + "/" +
                      common.GetUserIdFromAccessToken(myData.access_token) + "?nocache=" + Time.time;
            common.GetReq(url).ContinueWith(t =>
            {
                if (t.IsFaulted)
                    foreach (var innerException in t.Exception.InnerExceptions)
                        MyCommonUtil.de(innerException.Message);
                else
                    MyCommonUtil.de(t.Result);
            });
            var url2 = "/api/room/leave/" + myShared.roomId + "/" +
                      common.GetUserIdFromAccessToken(myData.access_token) + "?nocache=" + Time.time;
            common.GetReq(url2).ContinueWith(t =>
            {
                if (t.IsFaulted)
                    foreach (var innerException in t.Exception.InnerExceptions)
                        MyCommonUtil.de(innerException.Message);
                else
                    MyCommonUtil.de(t.Result);
            });
            // for avoid call normal leave api
            myShared.roomid = "";
            if (!isQuit) return;
            Application.Quit();
        }

        /// <summary>
        ///     シーンのクローズ
        /// </summary>
        public void Close()
        {
            _started = false;
            try
            {
                aiVoice.Stop();
                if (null == deviceName)
                {
                    deviceName = Microphone.devices[0];
                }
                //Microphone.End(deviceName);
                Resources
                    .FindObjectsOfTypeAll<GameObject>()
                    .FirstOrDefault(g => g.name == "modalBG")
                    ?.SetActive(true);
            }
            catch (Exception e)
            {
                Debug.LogError(e.Message);
            }

            try
            {
                LeaveRoom();
            }
            catch (Exception e)
            {
                Debug.LogError(e.Message);
            }
            Application.wantsToQuit -= WantsToQuit;
            Invoke(nameof(GotoMenu), 1.0f);
        }

        protected void GotoMenu()
        {
            try
            {
                if (null == deviceName)
                {
                    deviceName = Microphone.devices[0];
                }

                //Microphone.End(deviceName);
            }
            catch (Exception e)
            {
                Debug.LogError(e.Message);
            }
            SceneManager.LoadScene("searchrooms");
        }

        private void StartVoiceDetector()
        {
            deviceName = Microphone.devices[0];
            var go = GameObject.Find("UserVoice");
            DontDestroyOnLoad(go);
            audioSource = go.GetComponent<AudioSource>();
            audioSource.clip = Microphone.Start(deviceName, true, 10, SampleRate);
#if !UNITY_WEBGL
            while (!(Microphone.GetPosition(deviceName) > 0))
            {
            }
#endif
            audioSource.Play();
        }

        private void HandleDisplay()
        {
            goRecSign.SetActive(false);
            goComSign.SetActive(false);
            goAISign.SetActive(false);

            if (_started == false) return;

            if (isAITalking) goAISign.SetActive(true);

            if (isRecording) goRecSign.SetActive(true);

            if (isCommunicating) goComSign.SetActive(true);
        }

        /// <summary>
        ///     録音について
        /// </summary>
        [Obsolete("Obsolete")]
        private void HandleRecord()
        {
            if (_started == false) return;
            // AIが話しているときは録音しない
            if (isAITalking)
                return;
            // 通信中は録音しない
            if (isCommunicating) return;
            switch (isRecording)
            {
                case false when GetAverageVolume() > threshold:
                {
                    isRecording = true;

                    // Get the position 0.5 seconds ago
                    //var halfSecondAgo = Microphone.GetPosition(null) - SampleRate / 2;
                    var halfSecondAgo = Microphone.GetPosition(deviceName) - SampleRate / 2;
                    if (halfSecondAgo < 0) halfSecondAgo += audioSource.clip.samples;

                    // Get the data from 0.5 seconds ago to now
                    //var sampleLength = Microphone.GetPosition(null) - halfSecondAgo;
                    var sampleLength = Microphone.GetPosition(deviceName) - halfSecondAgo;
                    if (sampleLength < 0) sampleLength += audioSource.clip.samples;

                    var samples = new float[sampleLength];

#if UNITY_WEBGL && !UNITY_EDITOR
                    Microphone.GetData(samples, halfSecondAgo);
#else
                    audioSource.clip.GetData(samples, halfSecondAgo);
#endif
                    _audioData.AddRange(samples);

                    _lastSample = Microphone.GetPosition(deviceName);
                    break;
                }
                case true:
                {
                    var currentPos = Microphone.GetPosition(deviceName);
                    var sampleLength = currentPos >= _lastSample
                        ? currentPos - _lastSample
                        : audioSource.clip.samples - _lastSample + currentPos;

                    if (sampleLength == 0) return;
                    var samples = new float[sampleLength];
#if UNITY_WEBGL && !UNITY_EDITOR
                    Microphone.GetData(samples, _lastSample);
#else
                    audioSource.clip.GetData(samples, _lastSample);
#endif
                    _audioData.AddRange(samples);
                    _lastSample = currentPos;
                    if (GetAverageVolume() <= threshold)
                    {
                        if (_timeWhenVolumeBelowThreshold == 0)
                        {
                            // Start the timer when the volume goes below the threshold
                            _timeWhenVolumeBelowThreshold = Time.time;
                        }
                        else if (Time.time - _timeWhenVolumeBelowThreshold > gaman)
                        {
                            // Stop recording if 1 second has passed since the volume went below the threshold
                            isRecording = false;
                            UserAudioHandle();
                            _audioData.Clear();
                        }
                    }
                    else
                    {
                        _timeWhenVolumeBelowThreshold = 0;
                    }

                    break;
                }
            }
        }

        private int _lastSample4vol = 0;
        private float GetAverageVolume()
        {
            if (null == deviceName)
            {
                return 0f;
            }
            var currentPos = Microphone.GetPosition(deviceName);
            var offset = currentPos - 256 > 0 ? currentPos - 256 : 0;
            var length = 256;
            var samples = new float[length];
#if UNITY_WEBGL && !UNITY_EDITOR
            Microphone.GetData(samples, offset);
#else
            audioSource.clip.GetData(samples, offset);
#endif
            var sum = samples.Sum(Mathf.Abs);
            volume = sum / samples.Length;
            return volume;
        }

        [Obsolete("Obsolete")]
        private void UserAudioHandle()
        {
            // save as wav and ogg
            var oggBase64String = AudioUtility.GetOggBase64String(_audioData, SampleRate);
            StartCoroutine(GetTextFromAPI(oggBase64String));
        }

        // ReSharper disable Unity.PerformanceAnalysis
        [Obsolete("Obsolete")]
        private IEnumerator GetTextFromAPI(string oggBase64String)
        {
            isCommunicating = true;
            pauseTimer = true;
            var payLoad = new VoiceDto
            {
                user_id = common.GetUserIdFromAccessToken(myData.access_token),
                roomid = myShared.roomid,
                audio_base64 = oggBase64String
            };
#if UNITY_EDITOR
            if (myShared.roomid == "")
            {
                payLoad.user_id = "1";
                payLoad.roomid = DUMMY_ROOM_ID;
            }
#endif
            // Start the async task
            var task = common.PostReq("/api/ai_room/get_text_from_audio" + "?nocache=" + Time.time, payLoad);

            // Wait for the task to complete
            yield return task.AsCoroutine(result =>
            {
                MyCommonUtil.de(result);
                var voiceResult = JsonUtility.FromJson<VoiceDto>(result);
                if (voiceResult.result != "success") return;
                var txt = voiceResult.text;
                if (txt.Trim().Length >= MinimalResponseLen
                    && txt.Trim() != "ご視聴ありがとうございました"
                    && txt.Trim() != "ご視聴ありがとうございました。"
                    && txt.Trim() != "視聴してくださって 本当にありがとうございます。"
                    && txt.Trim() != "bye,H."
                    && !txt.Trim().Contains("視聴")
                   )
                {
                    AddChat(From.User, txt.Trim());
                    StartCoroutine(GetResponseFromAPI(txt));
                }
                else
                {
                    pauseTimer = false;
                    isCommunicating = false;
                }
            });

            // Check if task has exceptions
            if (task.Exception != null)
            {
                Debug.Log($"An error occurred: {task.Exception.ToString()}");
                // Handle exception as needed
                pauseTimer = false;
                isCommunicating = false;
            }

        }

        enum From
        {
            AI,
            User
        }

        private void AddChat(From from, string message)
        {
            const string resourceName = "MESSAGE_USER";
            var createdAt = DateTime.Now.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ");
            var nm = from == From.User ? myShared.userName : "AI Host";
            var coin = from == From.User ? 0 : 100;
            var chat = (GameObject)Instantiate(Resources.Load(resourceName), chatList.transform, false);
            chat.GetComponent<MiniAboutMessageUser>().InitialSet(coin, message, createdAt, nm);
            StartCoroutine(MyCommonUtil.ExecuteAfterDelay(1.0f, () => { _verticalScrollbar.value = 0f; }));
        }

        [Obsolete("Obsolete")]
        private IEnumerator GetResponseFromAPI(string text)
        {
            isCommunicating = true;
            pauseTimer = true;
            var payLoad = new VoiceDto
            {
                user_id = common.GetUserIdFromAccessToken(myData.access_token),
                roomid = myShared.roomid,
                text = text
            };
#if UNITY_EDITOR
            if (myShared.roomid == "")
            {
                payLoad.user_id = "1";
                payLoad.roomid = DUMMY_ROOM_ID;
            }
#endif
            // Start the async task
            var task = common.PostReq("/api/ai_room/get_response_wave_from_text" + "?nocache=" + Time.time, payLoad);

            // Wait for the task to complete
            yield return task.AsCoroutine(result =>
                {
                    var res = JsonUtility.FromJson<VoiceResponseDto>(result);
                    AddChat(From.AI, res.text);
                    isCommunicating = false;
                    HandleSmile(res.GetMaxEmotion());
                    PlayAudioFromBase64String(res.ogg_base64);
                },
                exception =>
                {
                    pauseTimer = false;
                    isCommunicating = false;
                });

            // Check if task has exceptions
            if (task.Exception != null)
            {
                Debug.Log($"An error occurred: {task.Exception.ToString()}");
                // Handle exception as needed
                pauseTimer = false;
                isCommunicating = false;
            }
        }

        // ReSharper disable Unity.PerformanceAnalysis
        [Obsolete("Obsolete")]
        private void PlayAudioFromBase64String(string base64String)
        {
            PlayAudioFromBase64StringCoroutine(base64String);
        }

        private void HandleSmile(Dictionary<string, float> smile)
        {
            blendShapeProxy.ImmediatelySetValue(BlendShapePreset.Joy, smile["joy"]);
            blendShapeProxy.ImmediatelySetValue(BlendShapePreset.Angry, smile["anger"]);
            blendShapeProxy.ImmediatelySetValue(BlendShapePreset.Sorrow, smile["sorrow"]);
            blendShapeProxy.ImmediatelySetValue(BlendShapePreset.Fun, smile["happy"]);
        }

        [Obsolete("Obsolete")]
        private void PlayAudioFromBase64StringCoroutine(string base64String)
        {
            isAITalking = true;
            pauseTimer = true;
            //var tempFilePath = Path.GetTempFileName() + ".ogg";
            //var tempFilePath =  "ai.ogg";
            var audioBytes = Convert.FromBase64String(base64String);

            using( var vorbis = new NVorbis.VorbisReader( new MemoryStream( audioBytes, false ) ) )
            {
                Debug.Log( $"Found ogg ch={vorbis.Channels} freq={vorbis.SampleRate} samp={vorbis.TotalSamples}" );
                float[] _audioBuffer = new float[vorbis.TotalSamples]; // Just dump everything
                int read = vorbis.ReadSamples( _audioBuffer, 0, (int)vorbis.TotalSamples );
                AudioClip audioClip = AudioClip.Create("AI", (int)(vorbis.TotalSamples / vorbis.Channels), vorbis.Channels, vorbis.SampleRate, false);
                audioClip.SetData( _audioBuffer, 0 );
                aiVoice.clip = audioClip;
                aiVoice.Play();
                StartCoroutine(WaitForAudioToFinish());
            }
        }

        [Obsolete("Obsolete")]
        private IEnumerator WaitForAudioToFinish()
        {
            var samples = new float[1024];
            while (aiVoice.isPlaying)
            {
                aiVoice.GetOutputData(samples, 0); // get the audio source output data
                var vol = samples.Sum(Mathf.Abs);
                vol /= samples.Length; // get the average volume

                var o = Mathf.Clamp(10f * vol, 0f, 1f);
                blendShapeProxy.ImmediatelySetValue(BlendShapePreset.O, o);
                yield return null;
            }

            // Audio has finished playing
            aiVoice.Stop();
            aiVoice.clip = null;
            blendShapeProxy.ImmediatelySetValue(BlendShapePreset.A, 0);
            isAITalking = false;
            pauseTimer = false;
        }
    }
}
