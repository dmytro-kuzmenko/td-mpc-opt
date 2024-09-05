import os
import time
from datetime import timedelta
os.environ['MUJOCO_GL'] = 'egl'
import warnings
warnings.filterwarnings('ignore')

import wandb
import hydra
import imageio
import numpy as np
import torch
from termcolor import colored
from tqdm import tqdm

from common.parser import parse_cfg
from common.seed import set_seed
from envs import make_env
from omegaconf import OmegaConf
from tdmpc2 import TDMPC2

torch.backends.cudnn.benchmark = True

def format_time(seconds):
    return str(timedelta(seconds=int(seconds)))

# config, config_multi_distill
@hydra.main(config_name='config_multi_distill', config_path='.')
def evaluate(cfg: dict):
	"""
	Script for evaluating a single-task / multi-task TD-MPC2 checkpoint.

	Most relevant args:
		`task`: task name (or mt30/mt80 for multi-task evaluation)
		`model_size`: model size, must be one of `[1, 5, 19, 48, 317]` (default: 5)
		`checkpoint`: path to model checkpoint to load
		`eval_episodes`: number of episodes to evaluate on per task (default: 10)
		`save_video`: whether to save a video of the evaluation (default: True)
		`seed`: random seed (default: 1)
	
	See config.yaml for a full list of args.

	Example usage:
	````
		$ python evaluate.py task=mt80 model_size=48 checkpoint=/path/to/mt80-48M.pt
		$ python evaluate.py task=mt30 model_size=317 checkpoint=/path/to/mt30-317M.pt
		$ python evaluate.py task=dog-run checkpoint=/path/to/dog-1.pt save_video=true
	```
	"""
	assert torch.cuda.is_available()
	assert cfg.eval_episodes > 0, 'Must evaluate at least 1 episode.'
	cfg = parse_cfg(cfg)
	set_seed(cfg.seed)

	wandb.init(
        project="worldmodels",
        config=OmegaConf.to_container(cfg, resolve=True),
        name=f"{cfg.task}_{cfg.get('model_size', 'default')}M",
        tags=[cfg.task, f"{cfg.get('model_size', 'default')}M"],
    )

	print(colored(f'Task: {cfg.task}', 'blue', attrs=['bold']))
	print(colored(f'Model size: {cfg.get("model_size", "default")}', 'blue', attrs=['bold']))
	print(colored(f'Checkpoint: {cfg.checkpoint}', 'blue', attrs=['bold']))
	if not cfg.multitask and ('mt80' in cfg.checkpoint or 'mt30' in cfg.checkpoint):
		print(colored('Warning: single-task evaluation of multi-task models is not currently supported.', 'red', attrs=['bold']))
		print(colored('To evaluate a multi-task model, use task=mt80 or task=mt30.', 'red', attrs=['bold']))


	cfg.model_size = 1 # only for multi
	cfg.mlp_dim = 384 # 512 
	cfg.latent_dim = 128 # 512
	cfg.num_q = 2 # 5
	# distillation
	cfg.distillation_temperature = 2.0
	cfg.distillation_weight = 0.5

	# Make environment
	env = make_env(cfg)

	cfg.model_size = 1 # only for multi
	cfg.num_enc_layers = 2
	cfg.enc_dim = 256
	cfg.mlp_dim = 384 # 384 # 512 
	cfg.latent_dim = 128 # 128 # 512
	cfg.num_q = 2 # 2 # 5
	# distillation
	cfg.distillation_temperature = 2.0
	cfg.distillation_weight = 0.5

	# Load agent
	agent = TDMPC2(cfg)

	assert os.path.exists(cfg.checkpoint), f'Checkpoint {cfg.checkpoint} not found! Must be a valid filepath.'
	agent.load(cfg.checkpoint)
	
	# Evaluate
	if cfg.multitask:
		print(colored(f'Evaluating agent on {len(cfg.tasks)} tasks:', 'yellow', attrs=['bold']))
	else:
		print(colored(f'Evaluating agent on {cfg.task}:', 'yellow', attrs=['bold']))
	if cfg.save_video:
		video_dir = os.path.join(cfg.work_dir, 'videos')
		os.makedirs(video_dir, exist_ok=True)
	scores = []
	tasks = cfg.tasks if cfg.multitask else [cfg.task]

	# table = wandb.Table(columns=["task", "reward", "time"])

	for task_idx, task in enumerate(tasks):
		start_time = time.time()
		# print(f'{task} started')
		if not cfg.multitask:
			task_idx = None
		ep_rewards, ep_successes = [], []
		for i in range(cfg.eval_episodes):
			obs, done, ep_reward, t = env.reset(task_idx=task_idx), False, 0, 0
			if cfg.save_video:
				frames = [env.render()]

			while not done:
				action = agent.act(obs, t0=t==0, task=task_idx)
				obs, reward, done, info = env.step(action)
				ep_reward += reward
				t += 1
				if cfg.save_video:
					frames.append(env.render())
			ep_rewards.append(ep_reward)
			ep_successes.append(info['success'])

			# Log intermediate results for this episode
			# wandb.log({
			# 	f"{task}/episode_reward": ep_reward,
			# 	# f"{task}/episode_success": info['success'],
			# 	# f"{task}/episode_length": t,
			# 	# f"{task}/step_rewards": wandb.Histogram(step_rewards),
			# }, step=i)

			if cfg.save_video:
				###
				print('saving video')
				imageio.mimsave(os.path.join(video_dir, f'{task}-{i}.mp4'), frames, fps=15)

		ep_rewards = np.mean(ep_rewards)
		ep_successes = np.mean(ep_successes)

		if cfg.multitask:
			scores.append(ep_successes*100 if task.startswith('mw-') else ep_rewards/10)
		print(colored(f'  {task:<22}' \
			f'\tR: {ep_rewards:.01f}  ' \
			f'\tS: {ep_successes:.02f}', 'yellow'))
		inference_time = time.time() - start_time
		# print(format_time(inference_time))
		# table.add_data(task, ep_rewards, format_time(inference_time))

		# wandb.log({"task_results": table})
	# wandb.log({"task_results": table})

	if cfg.multitask:
		print(colored(f'Normalized score: {np.mean(scores):.02f}', 'yellow', attrs=['bold']))


if __name__ == '__main__':
	evaluate()
	wandb.finish()