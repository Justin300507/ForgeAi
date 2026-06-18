import { useState } from 'react';
import MessageList from '../components/MessageList';
import InputBox from '../components/InputBox';
import UserList from '../components/UserList';

export default function Chat() {
  const [messages, setMessages] = useState([]);

  const handleSend = (message) => {
    setMessages([...messages, message]);
  };

  return (
    <div>
      <h1>Chat</h1>
      <UserList />
      <MessageList messages={messages} />
      <InputBox onSend={handleSend} />
    </div>
  );
}