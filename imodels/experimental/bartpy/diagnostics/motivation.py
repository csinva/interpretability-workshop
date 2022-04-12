import argparse
import itertools
import os
from functools import partial
from typing import List, Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import cm
from mpl_toolkits.axes_grid1 import make_axes_locatable
from tqdm import tqdm
from sklearn import model_selection, datasets
from sklearn.metrics import mean_squared_error
from sklearn.ensemble import GradientBoostingRegressor

from imodels import get_clean_dataset
from imodels.experimental.bartpy.initializers.sklearntreeinitializer import SklearnTreeInitializer
from imodels.experimental.bartpy.model import Model
from imodels.experimental.bartpy.tree import Tree
from ..sklearnmodel import BART, SklearnModel

ART_PATH = "/accounts/campus/omer_ronen/projects/tree_shrink/imodels/art"
DATASETS_REGRESSION = [
    # leo-breiman paper random forest uses some UCI datasets as well
    # pg 23: https://www.stat.berkeley.edu/~breiman/randomforest2001.pdf
    ('friedman1', 'friedman1', 'synthetic'),
    ('friedman2', 'friedman2', 'synthetic'),
    ('friedman3', 'friedman3', 'synthetic'),
    ('abalone', '183', 'openml'),
    ("diabetes-regr", "diabetes", 'sklearn'),
    ("california-housing", "california_housing", 'sklearn'),  # this replaced boston-housing due to ethical issues
    ("satellite-image", "294_satellite_image", 'pmlb'),
    ("echo-months", "1199_BNG_echoMonths", 'pmlb'),
    ("breast-tumor", "1201_BNG_breastTumor", 'pmlb'),  # this one is v big (100k examples)

]


def parse_args():
    parser = argparse.ArgumentParser(description='BART Research motivation')
    parser.add_argument('datasets', metavar='datasets', type=str,
                        help='dataset to run sim over')

    args = parser.parse_args()
    return args


def log_rmse(x, y):
    return np.log(np.sqrt(mean_squared_error(x, y)))


def mse_functional(model: SklearnModel, sample: Model, X, y):
    predictions_transformed = sample.predict(X)
    predictions = model.data.y.unnormalize_y(predictions_transformed)
    return log_rmse(predictions, y)


def n_leaves_functional(model: SklearnModel, sample: Model, X, y):
    n_leaves = 0
    for tree in sample.trees:
        n_leaves += len(tree.leaf_nodes)
    return n_leaves / len(sample.trees)


def analyze_functional(models: Dict[str, SklearnModel], functional: callable, axs=None, name=None, X=None, y=None):
    if axs is None:
        _, axs = plt.subplots(2, 1)
    colors = {0: cm.Blues, 1: cm.Greens}
    for i, (mdl_name, model) in enumerate(models.items()):
        n_chains = model.n_chains
        chain_len = int(len(model.model_samples) / n_chains)
        color = iter(colors[i](np.linspace(0.3, 0.7, n_chains)))

        functional_specific = partial(functional, X=X, y=y, model=model)
        hist_len = int(chain_len / 3)

        plt_data = {"plot": [], "hist": []}
        min_hist = np.inf
        max_hist = -1 * np.inf
        for c in range(n_chains):
            chain_sample = model.model_samples[c * chain_len:(c + 1) * chain_len]
            chain_functional = [functional_specific(sample=s) for s in chain_sample]
            plt_data['plot'].append((np.arange(chain_len), chain_functional))
            hist_data = chain_functional[(chain_len - hist_len):chain_len]
            plt_data['hist'].append(hist_data)
            max_hist = np.maximum(max_hist, np.max(hist_data))
            min_hist = np.minimum(min_hist, np.min(hist_data))

        for i, c in enumerate(range(n_chains)):
            clr = next(color)
            plt_x, plt_y = plt_data['plot'][i]
            axs[0].plot(plt_x, plt_y, color=clr, label=f"Chain {c} ({mdl_name})")
            hist_x = plt_data['hist'][i]
            axs[1].hist(hist_x, color=clr, label=f"Chain {c} ({mdl_name})",
                        alpha=0.5, bins=50, range=[min_hist, max_hist])

    axs[0].set_ylabel(name)
    axs[0].set_xlabel("Iteration")

    axs[1].set_xlabel(name)
    axs[1].set_ylabel("Count")

    axs[0].legend()
    # ax.set_title(title)
    return axs


def plot_chains_leaves(model: SklearnModel, ax=None, title="Tree Structure/Prediction Variation", x_label=False, X=None,
                       y=None):
    if ax is None:
        _, ax = plt.subplots(1, 1)
    complexity = {i: [] for i in range(model.n_trees)}
    n_chains = model.n_chains
    for sample in model.model_samples:
        for i, tree in enumerate(sample.trees):
            complexity[i].append(len(tree.leaf_nodes))

    chain_len = int(len(model.model_samples) / n_chains)
    color = iter(cm.rainbow(np.linspace(0, 1, n_chains)))

    for c in range(n_chains):
        clr = next(color)
        chain_preds = model.predict_chain(X, c)
        chain_std = np.round(model.chain_mse_std(X, y, c), 2)
        mse_chain = np.round(mean_squared_error(chain_preds, y), 2)

        trees_chain = np.stack([complexity[t][c * chain_len:(c + 1) * chain_len] for t in range(model.n_trees)], axis=1)
        y_plt = np.mean(trees_chain, axis=1)
        ax.plot(np.arange(chain_len), y_plt, color=clr, label=f"Chain {c} (mse: {mse_chain} std: {chain_std})")

    ax.set_ylabel("# Leaves")
    if x_label:
        ax.set_xlabel("Iteration")
    ax.legend()
    ax.set_title(title)
    return ax


def plot_within_chain(models: Dict[str, SklearnModel], ax=None, title="Within Chain Variation", x_label=False, X=None,
                      y=None):
    if ax is None:
        _, ax = plt.subplots(1, 1)
    lines = {0: "-", 1: '--'}

    for i, (mdl_name, model) in enumerate(models.items()):
        n_chains = model.n_chains

        chain_len = int(len(model.model_samples) / n_chains)
        color = iter(cm.rainbow(np.linspace(0, 1, n_chains)))

        for c in range(n_chains):
            clr = next(color)
            chain_preds = model.chain_precitions(X, c)
            mean_pred = np.array(chain_preds).mean(axis=0)

            y_plt = [np.log(np.sqrt(mean_squared_error(mean_pred, p))) for p in chain_preds]
            ax.plot(np.arange(chain_len), y_plt, color=clr,
                    label=f"Chain {c}, {mdl_name} (Average {np.round(np.mean(y_plt), 2)})", linestyle=lines[i])

    ax.set_ylabel("mean squared distance to average iteration")
    if x_label:
        ax.set_xlabel("Iteration")
    ax.legend()
    ax.set_title(title)
    return ax


def plot_across_chains(models: Dict[str, SklearnModel], ax=None, title="Across Chain Variation", x_label=False, X=None,
                       y=None, fig=None):
    if ax is None:
        _, ax = plt.subplots(1, 1)

    model_0 = list(models.values())[0]

    n_chains = model_0.n_chains * len(models)

    preds = []
    mat = np.zeros(shape=(n_chains, n_chains + 1))
    label_list = []

    for j, (mdl_name, model) in enumerate(models.items()):
        for c in range(model.n_chains):
            chain_preds = model.chain_precitions(X, c)
            chain_len = len(chain_preds)
            s = (chain_len - int(chain_len / 3))
            preds.append(model.predict_chain(X, c, s))

            posterior = chain_preds[s:chain_len]

            mean_pred = np.array(posterior).mean(axis=0)

            within_chain_mse = [log_rmse(mean_pred, p) for p in posterior]
            mat[j, 0] = np.round(np.mean(within_chain_mse), 2)
            label_list.append(f"Chain {c} ({mdl_name})")

    for c_i, c_j in itertools.combinations(range(n_chains), 2):
        mat[c_i, c_j + 1] = log_rmse(preds[c_i], preds[c_j])
        mat[c_j, c_i + 1] = log_rmse(preds[c_i], preds[c_j])
    im = ax.matshow(mat)
    # for c_i, c_j in itertools.combinations(range(n_chains), 2):
    #     c = np.round(mat[c_i, c_j], 2)
    #     ax.text(c_i, c_j, c, va='center', ha='center')
    #     ax.text(c_j, c_i, c, va='center', ha='center')
    ax.set_xlabel("log root mean squared distance between predictions")

    ax.set_xticks(np.arange(0, len(label_list) + 1))
    ax.set_xticklabels(["Within"] + label_list, rotation=90)

    ax.set_yticks(np.arange(0, len(label_list)))
    ax.set_yticklabels(label_list)

    ax.set_title(title)
    divider = make_axes_locatable(ax)
    cax = divider.new_vertical(size="5%", pad=0.7, pack_start=True)
    fig.add_axes(cax)
    fig.colorbar(im, cax=cax, orientation="horizontal")
    # ax.set_title(f"{title} (Between Chains Var {np.round(model.between_chains_var(X), 2)})")
    return ax


def main():
    n_trees = 50
    n_samples = 7500
    n_burn = 0  # 10000
    n_chains = 3
    args = parse_args()
    ds = args.datasets
    d = [d for d in DATASETS_REGRESSION if d[0] == ds]
    with tqdm(d) as t:
        for d in t:
            t.set_description(f'{d[0]}')
            X, y, feat_names = get_clean_dataset(d[1], data_source=d[2])
            n = len(y)
            p = X.shape[1]

            X_train, X_test, y_train, y_test = model_selection.train_test_split(
                X, y, test_size=0.5, random_state=4)

            bart_zero = BART(classification=False, store_acceptance_trace=True, n_trees=n_trees, n_samples=n_samples,
                             n_burn=n_burn, n_chains=n_chains, thin=1)
            bart_zero.fit(X_train, y_train)

            sgb = GradientBoostingRegressor(n_estimators=n_trees)
            sgb.fit(X_train, bart_zero.data.y.values)

            bart_sgb = BART(classification=False, store_acceptance_trace=True, n_trees=n_trees, n_samples=n_samples,
                            n_burn=n_burn, n_chains=n_chains, thin=1, initializer=SklearnTreeInitializer(tree_=sgb))
            bart_sgb.fit(X_train, y_train)

            fig, axs = plt.subplots(3, 2, figsize=(10, 22))
            # fig.tight_layout()
            fig.subplots_adjust(hspace=.6)

            barts = {"SGB": bart_sgb, "Single Leaf": bart_zero}

            # plot_chains_leaves(bart_zero, axs[0], X=X_test, y=y_test)
            analyze_functional(barts, functional=mse_functional, axs=axs[0, 0:2], X=X_test, y=y_test,
                               name="Test log-RMSE")
            analyze_functional(barts, functional=n_leaves_functional, axs=axs[1, 0:2], X=X_test, y=y_test,
                               name="# Leaves")
            # plot_within_chain(barts, axs[2], X=X_test, y=y_test)
            plot_across_chains(barts, axs[2, 1], X=X_test, y=y_test, fig=fig)
            axs[2, 0].axis('off')

            #
            title = f"Dataset: {d[0].capitalize()}, (n, p) = ({n}, {p}), burn = {n_burn}"
            plt.suptitle(title)
            #
            plt.savefig(os.path.join(ART_PATH, "functional", f"{d[0]}_samples_{n_samples}_new.png"))
            plt.close()


if __name__ == '__main__':
    main()
