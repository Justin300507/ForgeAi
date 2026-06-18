import React, { useState, useEffect } from 'react';
import TaskList from '../components/TaskList';
import TaskForm from '../components/TaskForm';

export default function Dashboard() {
  const [tasks, setTasks] = useState([]);

  useEffect(() => {
    // Fetch tasks from API
    const mockTasks = [
      { id: 1, title: 'Task 1', priority: 1 },
      { id: 2, title: 'Task 2', priority: 2 }
    ];
    setTasks(mockTasks);
  }, []);

  const addTask = (task) => {
    setTasks([...tasks, task]);
  };

  return (
    <div>
      <h1>Dashboard</h1>
      <TaskForm onSubmit={addTask} />
      <TaskList tasks={tasks} />
    </div>
  );
}