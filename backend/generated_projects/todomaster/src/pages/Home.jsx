import React, { useState, useEffect } from 'react';
import api from '../api';

export default function Home() {
  const [tasks, setTasks] = useState([]);

  useEffect(() => {
    api.get('/tasks').then(res => setTasks(res.data));
  }, []);

  return (
    <div>
      {tasks.map(task => (
        <div key={task.id}>{task.title}</div>
      ))}
    </div>
  );
}