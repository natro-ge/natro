import { mount } from 'svelte'
import './app.css'
import Dock from './Dock.svelte'

mount(Dock, { target: document.getElementById('app')! })
