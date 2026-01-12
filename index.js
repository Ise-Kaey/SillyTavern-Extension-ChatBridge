import { extension_settings, getContext } from "../../../extensions.js";
import { saveSettingsDebounced } from "../../../../script.js";
import { chat } from "../../../../script.js";

const extensionName = "SillyTavern-Extension-ChatBridge";
const defaultSettings = {
    wsPort: 8001,
    autoConnect: false
};

if (!extension_settings[extensionName]) {
    extension_settings[extensionName] = {};
}

Object.assign(extension_settings[extensionName], defaultSettings);

let ws;

function updateDebugLog(message) {
    const debugLog = $('#debug_log');
    if (debugLog.length === 0) {
        console.warn('Debug log element not found');
        return;
    }
    const timestamp = new Date().toLocaleTimeString();
    const currentContent = debugLog.val();
    const newLine = `[${timestamp}] ${message}\n`;
    debugLog.val(currentContent + newLine);
    debugLog.scrollTop(debugLog[0].scrollHeight);
    // At the same time, it is output on the console to facilitate debugging.
    console.log(`[${extensionName}] ${message}`);
}

function updateWSStatus(connected) {
    const status = $('#ws_status');
    if (connected) {
        status.text('Connected').css('color', 'green');
    } else {
        status.text('Not connected').css('color', 'red');
    }
}
function convertOpenAIToSTMessage(msg) {
    const isUser = msg.role === 'user';
    const currentTime = new Date().toLocaleString();

    return {
        name: isUser ? 'user' : 'Assistant', // Note: Username must be lowercase
        is_user: isUser,
        is_system: false,
        send_date: currentTime,
        mes: msg.content,
        extra: {
            isSmallSys: false,
            token_count: 0,
            reasoning: ''
        },
        force_avatar: isUser ? "User Avatars/1739777502672-user.png" : null
    };
}

function setupWebSocket() {
    const wsUrl = $('#ws_url').val();
    const wsPort = $('#ws_port').val();
    updateDebugLog(`Try to connect to websocket server: ws://${wsUrl}:${wsPort}`);

    ws = new WebSocket(`ws://${wsUrl}:${wsPort}`);

    ws.onopen = () => {
        updateWSStatus(true);
        updateConnectionButtons(true);
        updateDebugLog('websocket connection established');
        //sendChatHistory();
    };
    ws.onmessage = async (event) => {
        try {
            const data = JSON.parse(event.data);
            updateDebugLog(`message received: ${JSON.stringify(data)}`);

            if (data.type === 'user_request') {
                updateDebugLog('User request received');
                if (data.content?.messages) {
                    const context = getContext();
                    const newChat = data.content.messages
                        .filter(msg => msg.role === 'user' || msg.role === 'assistant')
                        .map(msg => convertOpenAIToSTMessage(msg));

                    chat.splice(0, chat.length, ...newChat);
                    context.clearChat();
                    context.printMessages();
                    context.eventSource.emit(context.eventTypes.CHAT_CHANGED, context.getCurrentChatId());
                    updateDebugLog(`Chat content has been updated, total ${context.chat.length} messages`);
                    $('#send_but').click();
                } else {
                    updateDebugLog('Error: Message format is incorrect');
                }
            }
        } catch (error) {
            updateDebugLog(`An error occurred while processing the message: ${error.message}`);
            console.error(error); // Output complete error message
        }
    };
    ws.onclose = () => {
        updateWSStatus(false);
        updateConnectionButtons(false);
        updateDebugLog('websocket connection closed');
    };

    ws.onerror = (error) => {
        updateWSStatus(false);
        updateDebugLog(`websocket error: ${error}`);
    };
}

function updateConnectionButtons(connected) {
    $('#ws_connect').prop('disabled', connected);
    $('#ws_disconnect').prop('disabled', !connected);
    $('#ws_url').prop('disabled', connected);
    $('#ws_port').prop('disabled', connected);
}

function disconnectWebSocket() {
    if (ws) {
        ws.close();
    }
    updateWSStatus(false);
    updateConnectionButtons(false);
    updateDebugLog('websocket connection disconnected');
    // If automatic connection attempts are enabled, start timing immediately
    if (extension_settings[extensionName].autoConnect) {
        startAutoConnect();
    }
}

//Automatically try to connect
let autoConnectTimer = null;
//Automatically try connection function
function startAutoConnect() {
    if (autoConnectTimer) {
        clearInterval(autoConnectTimer);
    }
    
    autoConnectTimer = setInterval(() => {
        if (!ws || ws.readyState === WebSocket.CLOSED) {
            updateDebugLog('Automatically trying to connect...');
            setupWebSocket();
        }
    }, 5000);
}

function stopAutoConnect() {
    if (autoConnectTimer) {
        clearInterval(autoConnectTimer);
        autoConnectTimer = null;
    }
}



jQuery(async () => {

    // // Event system test code
    // const context = getContext();
    // updateDebugLog('=== Available event types ===');
    // for (const eventType in context.eventTypes) {
    //     updateDebugLog(`${eventType}: ${context.eventTypes[eventType]}`);
    // }

    const template = await $.get(`/scripts/extensions/third-party/${extensionName}/index.html`);
    $('#extensions_settings').append(template);

    $('#ws_connect').on('click', setupWebSocket);
    $('#ws_disconnect').on('click', disconnectWebSocket);
    $('#ws_port').val(extension_settings[extensionName].wsPort);

    $('#ws_port').on('change', function () {
        extension_settings[extensionName].wsPort = $(this).val();
        saveSettingsDebounced();
    });
    setupWebSocket();

    //Automatically try to connect
    $('#ws_auto_connect').prop('checked', extension_settings[extensionName].autoConnect);
    // Add event handling for the Automatically try to connect checkbox
    $('#ws_auto_connect').on('change', function() {
        const isChecked = $(this).prop('checked');
        extension_settings[extensionName].autoConnect = isChecked;
        saveSettingsDebounced();
        
        if (isChecked) {
            updateDebugLog('Automatic connection attempts enabled');
            startAutoConnect();
        } else {
            updateDebugLog('Automatic connection attempts disabled');
            stopAutoConnect();
        }
    });
    
    // If automatic connection attempts are enabled, start the timer
    if (extension_settings[extensionName].autoConnect) {
        startAutoConnect();
    }

    updateDebugLog('Extension initialization completed');

    // The following is the test code
    // $('#show_chat').on('click', () => {
    //     const context = getContext();

    //     updateDebugLog('=== Current chat status ===');
    //     updateDebugLog('name1: ' + context.name1);
    //     updateDebugLog('name2: ' + context.name2);
    //     updateDebugLog('characterId: ' + context.characterId);
    //     updateDebugLog('Current chat content:');
    //     updateDebugLog(JSON.stringify(context.chat, null, 2));
    //     updateDebugLog('Current chat metadata:');
    //     updateDebugLog(JSON.stringify(context.chatMetadata, null, 2));
    // });

    // $('#replace_chat').on('click', () => {
    //     const context = getContext();

    //     const nativeChat = [
    //         {
    //             "name": "user",
    //             "is_user": true,
    //             "is_system": false,
    //             "send_date": "February 26, 2025 2:09pm",
    //             "mes": "？",
    //             "extra": {
    //                 "isSmallSys": false,
    //                 "token_count": 2,
    //                 "reasoning": ""
    //             },
    //             "force_avatar": "User Avatars/1739777502672-user.png"
    //         },
    //         {
    //             "extra": {
    //                 "api": "custom",
    //                 "model": "gemini-2.0-flash-exp",
    //                 "reasoning": "",
    //                 "reasoning_duration": null,
    //                 "token_count": 64
    //             },
    //             "name": "test",
    //             "is_user": false,
    //             "send_date": "February 26, 2025 2:09pm",
    //             "mes": "I'm not quite sure what you're asking. can you explain your problem in more detail？",
    //             "title": "",
    //             "gen_started": "2025-02-26T06:09:43.173Z",
    //             "gen_finished": "2025-02-26T06:09:45.338Z",
    //             "swipe_id": 0,
    //             "swipes": ["I'm not quite sure what you're asking. Can you explain your problem in more detail?"],
    //             "swipe_info": [{
    //                 "send_date": "February 26, 2025 2:09pm",
    //                 "gen_started": "2025-02-26T06:09:43.173Z",
    //                 "gen_finished": "2025-02-26T06:09:45.338Z",
    //                 "extra": {
    //                     "api": "custom",
    //                     "model": "gemini-2.0-flash-exp",
    //                     "reasoning": "",
    //                     "reasoning_duration": null,
    //                     "token_count": 64
    //                 }
    //             }]
    //         }
    //     ];

    //     const nativeChat2 = [
    //         {
    //             "name": "user",
    //             "is_user": true,
    //             "is_system": false,
    //             "send_date": "February 28, 2025 12:43am",
    //             "mes": "?",
    //             "extra": {
    //                 "isSmallSys": false,
    //                 "token_count": 2,
    //                 "reasoning": ""
    //             },
    //             "force_avatar": "User Avatars/1739777502672-user.png"
    //         },
    //         {
    //             "extra": {
    //                 "api": "custom",
    //                 "model": "gemini-2.0-flash-exp",
    //                 "reasoning": "",
    //                 "reasoning_duration": null,
    //                 "token_count": 3
    //             },
    //             "name": "test",
    //             "is_user": false,
    //             "send_date": "February 28, 2025 12:43am",
    //             "mes": "Hello!?",
    //             "title": "",
    //             "gen_started": "2025-02-27T16:43:43.214Z",
    //             "gen_finished": "2025-02-27T16:43:45.973Z",
    //             "swipe_id": 0,
    //             "swipes": [
    //                 "Hello!?"
    //             ],
    //             "swipe_info": [
    //                 {
    //                     "send_date": "February 28, 2025 12:43am",
    //                     "gen_started": "2025-02-27T16:43:43.214Z",
    //                     "gen_finished": "2025-02-27T16:43:45.973Z",
    //                     "extra": {
    //                         "api": "custom",
    //                         "model": "gemini-2.0-flash-exp",
    //                         "reasoning": "",
    //                         "reasoning_duration": null,
    //                         "token_count": 3
    //                     }
    //                 }
    //             ]
    //         }
    //     ];


    //     try {
    //         //New conversations must be enabled first
    //         //Clear first and then add
    //         chat.splice(0, chat.length, ...nativeChat);
    //         //chat.splice(0, chat.length, ...nativeChat2);
    //         context.clearChat();
    //         context.printMessages();
    //         context.eventSource.emit(context.eventTypes.CHAT_CHANGED, context.getCurrentChatId());
            
    //     } catch (error) {
    //         updateDebugLog(`Error while replacing chat: ${error.message}`);
    //         console.error(error);
    //     }
    // });

    // updateDebugLog('Test function has been initialized');
});
